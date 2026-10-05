"""The routine scheduler (M17-04) on the in-memory stores, a fake clock and the fake model."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from langchain_core.messages import HumanMessage
from langgraph.checkpoint.memory import MemorySaver
from starlette.testclient import TestClient

from app.agent.turn_runner import TurnOutcome, TurnRequest
from app.api import routines as routines_api
from app.core.delegation import DelegationDenied
from app.core.identity import IDENTITY_HEADER, Identity, IdentityError
from app.db.routines import INTERRUPTED
from app.db.settings import InMemorySettingsStore
from app.db.threads import InMemoryThreadStore
from app.main import create_app
from app.routines.runs import _ended
from app.routines.scheduler import RoutineScheduler
from tests.fake_model.scripting import ErrorTurn, FakeModel, TextTurn
from tests.fake_platform.scripting import FakePlatform

ALICE = "00000000-0000-4000-8000-0000000000a1"
HEADERS = {IDENTITY_HEADER: "alice-token"}
NOW = datetime(2026, 10, 5, 14, 0, tzinfo=UTC)  # Mon 07:00 PDT
DUE = datetime(2026, 10, 5, 15, 30, tzinfo=UTC)  # weekdays 08:30 PDT
NEXT = datetime(2026, 10, 6, 15, 30, tzinfo=UTC)


class Verifier:
    async def verify(self, token: str | None) -> Identity:
        if token != "alice-token":
            raise IdentityError("unknown")
        return Identity(user_id=ALICE, session_id="s-alice", role="member")


class Clock:
    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


@pytest.fixture(autouse=True)
def _fixed_now(monkeypatch) -> None:
    monkeypatch.setattr(routines_api, "_now", lambda: NOW)


@pytest.fixture
def client(fake_model: FakeModel, fake_platform: FakePlatform):
    fake_platform.identities["alice-token"] = (ALICE, "s-alice")
    app = create_app(
        fake_model.settings(
            platform_url=fake_platform.base_url, routines_scheduler_enabled=False
        ),
        checkpointer_override=MemorySaver(),
        thread_store_override=InMemoryThreadStore(),
        settings_store_override=InMemorySettingsStore(),
        identity_verifier_override=Verifier(),
        delegation_client_override=fake_platform.client(),
    )
    with TestClient(app) as test_client:
        yield test_client


class Harness:
    """A scheduler on the app's state, driven by hand: `tick`, then `idle`."""

    def __init__(self, client: TestClient, clock: Clock, **kw) -> None:
        self.client = client
        self.clock = clock
        options = {"poll_s": 3600, "max_concurrent": 1, "grace": timedelta(hours=1)}
        options |= {"run_timeout_s": 30} | kw
        self.scheduler = RoutineScheduler(client.app.state, clock=clock, **options)

    def call(self, fn, *args):
        return self.client.portal.call(fn, *args)

    def start(self) -> None:
        self.call(lambda: self.scheduler.start(poll=False))

    def tick(self) -> list:
        return self.call(self.scheduler.tick)

    def idle(self) -> None:
        self.call(lambda: asyncio.wait_for(self.scheduler.idle(), 20))

    def stop(self) -> None:
        self.call(self.scheduler.stop)


@pytest.fixture
def harness(client):
    made: list[Harness] = []

    def make(now: datetime = DUE + timedelta(seconds=5), **kw) -> Harness:
        made.append(Harness(client, Clock(now), **kw))
        return made[-1]

    yield make
    for h in made:
        h.stop()


def _create(client: TestClient, **overrides) -> dict:
    body = {
        "name": "Morning brief",
        "prompt": "Summarize my calendar.",
        "schedule": {"kind": "weekdays", "time": "08:30"},
        "timezone": "America/Los_Angeles",
        **overrides,
    }
    response = client.post("/api/routines", json=body, headers=HEADERS)
    assert response.status_code == 201, response.text
    return response.json()


def _routine(client: TestClient, routine_id: str) -> dict:
    return client.get(f"/api/routines/{routine_id}", headers=HEADERS).json()


def _runs(client: TestClient, routine_id: str) -> list[dict]:
    return client.get(f"/api/routines/{routine_id}/runs", headers=HEADERS).json()


def _reply(client: TestClient, thread_id: str) -> str:
    messages = client.get(f"/api/threads/{thread_id}/messages", headers=HEADERS).json()
    return messages[-1]["content"]


def test_a_due_routine_runs_once(client, harness, fake_model, fake_platform) -> None:
    routine = _create(client)
    assert routine["next_run_at"] == "2026-10-05T15:30:00Z"
    fake_model.queue(TextTurn("your brief"))
    h = harness()
    h.start()

    assert h.tick()[0].status == "queued"
    h.idle()
    (run,) = _runs(client, routine["id"])
    assert (run["trigger"], run["status"], run["detail"]) == ("schedule", "succeeded", None)
    assert run["due_at"] == "2026-10-05T15:30:00Z"
    assert _reply(client, run["thread_id"]) == "your brief"
    assert fake_platform.grant_exchanges[-1][1] == run["thread_id"]
    assert _routine(client, routine["id"])["next_run_at"] == "2026-10-06T15:30:00Z"

    # Nothing is due again: not on the next tick, nor after a restart.
    assert h.tick() == []
    assert harness().tick() == []
    assert len(_runs(client, routine["id"])) == 1


def test_a_restart_fails_the_running_run_and_requeues_the_queued_one(
    client, harness, fake_model
) -> None:
    routine = _create(client)
    store = client.app.state.routine_store
    record = client.portal.call(store.get, routine["id"], ALICE)
    cut_off = client.portal.call(store.create_run, record, "manual", "running")
    before = harness()
    assert [r.status for r in before.tick()] == ["queued"]  # claimed, never started

    fake_model.queue(TextTurn("after the restart"))
    after = harness()
    after.start()
    after.idle()
    runs = {r["id"]: r for r in _runs(client, routine["id"])}
    assert (runs[cut_off.id]["status"], runs[cut_off.id]["detail"]) == ("failed", INTERRUPTED)
    (requeued,) = [r for r in runs.values() if r["id"] != cut_off.id]
    assert requeued["status"] == "succeeded"
    assert _reply(client, requeued["thread_id"]) == "after the restart"


def test_a_long_outage_records_one_missed_run(client, harness) -> None:
    routine = _create(client)
    h = harness(DUE + timedelta(days=2, hours=3))  # Wed 11:30 PDT
    h.start()
    (missed,) = h.tick()
    h.idle()
    assert missed.status == "missed"
    (run,) = _runs(client, routine["id"])
    assert run["status"] == "missed" and run["thread_id"] is None
    assert run["detail"] == "not started within 1 h of its time"
    # It moves on to the next time after now; the skipped days never fire.
    assert _routine(client, routine["id"])["next_run_at"] == "2026-10-08T15:30:00Z"
    assert h.tick() == []


def test_a_late_run_within_the_grace_still_runs(client, harness, fake_model) -> None:
    routine = _create(client)
    fake_model.queue(TextTurn("a bit late"))
    h = harness(DUE + timedelta(minutes=50))
    h.start()
    h.tick()
    h.idle()
    assert _runs(client, routine["id"])[0]["status"] == "succeeded"


def test_runs_wait_for_a_free_slot_and_go_stale_in_a_backlog(
    client, harness, fake_model
) -> None:
    first = _create(client, name="First")
    second = _create(client, name="Second")
    fake_model.queue(TextTurn("slow " * 40, chunk_size=4, chunk_delay_s=0.02))
    h = harness()
    h.start()
    h.tick()

    store = client.app.state.routine_store

    async def outlast_the_grace():
        while (await store.list_runs(first["id"], ALICE))[0].status != "running":
            await asyncio.sleep(0.01)
        h.clock.now += timedelta(hours=2)

    h.call(outlast_the_grace)
    h.idle()
    assert _runs(client, first["id"])[0]["status"] == "succeeded"
    (stale,) = _runs(client, second["id"])
    assert (stale["status"], stale["detail"]) == ("missed", "waited too long for a free model slot")


def test_routines_wait_for_interactive_turns(client, harness, fake_model) -> None:
    routine = _create(client)
    fake_model.queue(TextTurn("chat " * 30, chunk_size=4, chunk_delay_s=0.02), TextTurn("brief"))
    state = client.app.state
    h = harness()
    h.start()

    async def chat_then_tick():
        thread = await state.thread_store.create(ALICE, "chat")
        turn, queue = await state.turn_runner.start(
            TurnRequest(
                thread_id=thread.id,
                user_id=ALICE,
                run_input={"messages": [HumanMessage(content="hi")]},
                hitl_enabled=False,
                thinking_enabled=False,
            )
        )
        turn.detach(queue)
        await h.scheduler.tick()
        await asyncio.sleep(0.1)
        waiting = (await state.routine_store.list_runs(routine["id"], ALICE))[0].status
        await turn.wait()
        return waiting

    assert h.call(chat_then_tick) == "queued"
    h.idle()
    (run,) = _runs(client, routine["id"])
    assert run["status"] == "succeeded"
    assert _reply(client, run["thread_id"]) == "brief"


def test_a_run_past_its_time_limit_is_cancelled(client, harness, fake_model) -> None:
    routine = _create(client)
    fake_model.queue(TextTurn("never ending " * 200, chunk_size=2, chunk_delay_s=0.02))
    h = harness(run_timeout_s=0.3)
    h.start()
    h.tick()
    h.idle()
    (run,) = _runs(client, routine["id"])
    assert run["status"] == "timed_out"
    assert run["finished_at"] is not None


def test_a_failed_run_is_recorded(client, harness, fake_model) -> None:
    routine = _create(client)
    fake_model.queue(ErrorTurn(400, {"error": {"message": "the model fell over"}}))
    h = harness()
    h.start()
    h.tick()
    h.idle()
    (run,) = _runs(client, routine["id"])
    assert run["status"] == "failed"
    assert run["detail"]


def test_a_one_shot_retires_itself(client, harness, fake_model, fake_platform) -> None:
    routine = _create(client, schedule={"kind": "once", "at": "2026-10-05T09:00"})
    assert routine["next_run_at"] == "2026-10-05T16:00:00Z"
    fake_model.queue(TextTurn("once and done"))
    h = harness(datetime(2026, 10, 5, 16, 0, 1, tzinfo=UTC))
    h.start()
    h.tick()
    h.idle()
    assert _runs(client, routine["id"])[0]["status"] == "succeeded"
    after = _routine(client, routine["id"])
    assert (after["enabled"], after["next_run_at"]) == (False, None)
    assert fake_platform.routine_grants == {}
    assert h.tick() == []


def test_a_revoked_grant_fails_the_run_and_turns_the_routine_off(
    client, harness, fake_platform
) -> None:
    routine = _create(client)
    (held,) = fake_platform.routine_grants.values()
    fake_platform.revoked_sessions.add(held.session_id)
    h = harness()
    h.start()
    h.tick()
    h.idle()
    (run,) = _runs(client, routine["id"])
    assert run["status"] == "failed" and run["thread_id"] is None
    assert "revoked" in run["detail"]
    assert _routine(client, routine["id"])["enabled"] is False
    assert client.get("/api/threads", headers=HEADERS).json() == []


def test_a_routine_turned_off_while_queued_is_skipped(client, harness) -> None:
    routine = _create(client)
    h = harness()
    h.tick()  # claimed; no workers yet
    client.patch(f"/api/routines/{routine['id']}", json={"enabled": False}, headers=HEADERS)
    h.start()
    h.idle()
    (run,) = _runs(client, routine["id"])
    assert (run["status"], run["detail"]) == ("missed", "the routine was turned off")


@pytest.mark.parametrize(
    ("outcome", "timed_out", "status"),
    [
        (TurnOutcome("completed"), False, "succeeded"),
        (TurnOutcome("awaiting_approval", {"id": "x"}), False, "waiting_approval"),
        (TurnOutcome("cancelled"), True, "timed_out"),
        (TurnOutcome("cancelled"), False, "failed"),
        (TurnOutcome("error", error=DelegationDenied("gone")), False, "failed"),
        (TurnOutcome("error", error=RuntimeError("boom")), False, "failed"),
    ],
)
def test_turn_outcomes_map_to_run_statuses(outcome, timed_out, status) -> None:
    assert _ended(outcome, timed_out)[0] == status


def test_a_routines_last_run_is_listed(client, harness, fake_model) -> None:
    routine = _create(client)
    assert routine["last_run"] is None
    fake_model.queue(TextTurn("your brief"))
    h = harness()
    h.start()
    h.tick()
    h.idle()
    (run,) = _runs(client, routine["id"])
    expected = {k: run[k] for k in ("id", "status", "finished_at", "thread_id")}
    (listed,) = client.get("/api/routines", headers=HEADERS).json()
    assert listed["last_run"] == expected
    assert _routine(client, routine["id"])["last_run"] == expected

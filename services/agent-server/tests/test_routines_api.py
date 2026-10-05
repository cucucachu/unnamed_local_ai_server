"""`/api/routines` (M17-02) on the in-memory stores, two users sharing one app."""

from __future__ import annotations

import time
from datetime import UTC, datetime

import pytest
from langgraph.checkpoint.memory import MemorySaver
from starlette.testclient import TestClient

from app.api import routines as routines_api
from app.core.identity import IDENTITY_HEADER, Identity, IdentityError
from app.db.settings import InMemorySettingsStore
from app.db.threads import InMemoryThreadStore
from app.main import create_app
from app.routines import runs
from tests.fake_model.scripting import FakeModel, TextTurn
from tests.fake_platform.scripting import FakePlatform

ALICE = "00000000-0000-4000-8000-0000000000a1"
BOB = "00000000-0000-4000-8000-0000000000b2"
USERS = {"alice-token": ALICE, "bob-token": BOB}
NOW = datetime(2026, 10, 5, 14, 0, tzinfo=UTC)  # Mon 07:00 PDT


class HeaderIdentityVerifier:
    async def verify(self, token: str | None) -> Identity:
        if token not in USERS:
            raise IdentityError("unknown")
        return Identity(user_id=USERS[token], session_id=f"s-{token}", role="member")


@pytest.fixture(autouse=True)
def _fixed_now(monkeypatch) -> None:
    monkeypatch.setattr(routines_api, "_now", lambda: NOW)


@pytest.fixture
def client(fake_model: FakeModel, fake_platform: FakePlatform):
    for token, user_id in USERS.items():
        fake_platform.identities[token] = (user_id, f"s-{token}")
    fake_platform.add_space("family", {ALICE: "editor", BOB: "viewer"})
    thread_store = InMemoryThreadStore()
    app = create_app(
        fake_model.settings(
            platform_url=fake_platform.base_url, routines_scheduler_enabled=False
        ),
        checkpointer_override=MemorySaver(),
        thread_store_override=thread_store,
        settings_store_override=InMemorySettingsStore(),
        identity_verifier_override=HeaderIdentityVerifier(),
        delegation_client_override=fake_platform.client(),
    )
    with TestClient(app) as test_client:
        yield test_client


def _as(token: str) -> dict[str, str]:
    return {IDENTITY_HEADER: token}


ALICE_H, BOB_H = _as("alice-token"), _as("bob-token")


def _routine(**overrides) -> dict:
    return {
        "name": "Morning brief",
        "prompt": "Summarize my calendar.",
        "schedule": {"kind": "weekdays", "time": "08:30"},
        "timezone": "America/Los_Angeles",
        **overrides,
    }


def _create(client: TestClient, headers=ALICE_H, **overrides) -> dict:
    response = client.post("/api/routines", json=_routine(**overrides), headers=headers)
    assert response.status_code == 201, response.text
    return response.json()


def test_create_computes_the_next_run(client) -> None:
    routine = _create(client)
    assert routine["space"] == "/personal"
    assert routine["enabled"] is True
    assert routine["schedule"] == {"kind": "weekdays", "time": "08:30:00"}
    assert routine["next_run_at"] == "2026-10-05T15:30:00Z"
    assert routine["last_run_at"] is None


def test_crud_round_trip(client) -> None:
    first = _create(client, name="b second")
    second = _create(client, name="A first")
    listing = client.get("/api/routines", headers=ALICE_H).json()
    assert [r["id"] for r in listing] == [second["id"], first["id"]]
    assert client.get(f"/api/routines/{first['id']}", headers=ALICE_H).json() == first

    patched = client.patch(
        f"/api/routines/{first['id']}",
        json={"name": "Renamed", "schedule": {"kind": "monthly", "day": 31, "time": "09:00"}},
        headers=ALICE_H,
    )
    assert patched.status_code == 200, patched.text
    body = patched.json()
    assert body["name"] == "Renamed"
    assert body["next_run_at"] == "2026-10-31T16:00:00Z"

    disabled = client.patch(
        f"/api/routines/{first['id']}", json={"enabled": False}, headers=ALICE_H
    ).json()
    assert disabled["enabled"] is False and disabled["next_run_at"] is None
    enabled = client.patch(
        f"/api/routines/{first['id']}", json={"enabled": True}, headers=ALICE_H
    ).json()
    assert enabled["next_run_at"] == "2026-10-31T16:00:00Z"

    assert client.delete(f"/api/routines/{first['id']}", headers=ALICE_H).status_code == 204
    assert client.get(f"/api/routines/{first['id']}", headers=ALICE_H).status_code == 404
    assert client.delete(f"/api/routines/{first['id']}", headers=ALICE_H).status_code == 404


def test_other_users_cant_see_or_touch_a_routine(client) -> None:
    routine = _create(client)
    path = f"/api/routines/{routine['id']}"
    assert client.get("/api/routines", headers=BOB_H).json() == []
    assert client.get(path, headers=BOB_H).status_code == 404
    assert client.patch(path, json={"name": "x"}, headers=BOB_H).status_code == 404
    assert client.post(f"{path}/run", headers=BOB_H).status_code == 404
    assert client.get(f"{path}/runs", headers=BOB_H).status_code == 404
    assert client.delete(path, headers=BOB_H).status_code == 404
    assert client.get(path, headers=ALICE_H).status_code == 200
    assert client.get("/api/routines").status_code == 401


def test_space_needs_edit_rights(client) -> None:
    shared = _create(client, space="spaces/Family/")
    assert shared["space"] == "/spaces/family"

    viewer = client.post("/api/routines", json=_routine(space="/spaces/family"), headers=BOB_H)
    assert viewer.status_code == 403
    unknown = client.post("/api/routines", json=_routine(space="/spaces/nope"), headers=ALICE_H)
    assert unknown.status_code == 422

    mine = _create(client, headers=BOB_H)
    moved = client.patch(
        f"/api/routines/{mine['id']}", json={"space": "/spaces/family"}, headers=BOB_H
    )
    assert moved.status_code == 403


@pytest.mark.parametrize(
    "overrides",
    [
        {"timezone": "Mars/Olympus"},
        {"schedule": {"kind": "daily"}},
        {"schedule": {"kind": "once", "at": "2026-10-01T08:00"}},
        {"name": "  "},
        {"prompt": ""},
        {"owner_user_id": BOB},
    ],
)
def test_invalid_routines_are_refused(client, overrides) -> None:
    response = client.post("/api/routines", json=_routine(**overrides), headers=ALICE_H)
    assert response.status_code == 422, response.text


def test_a_past_one_shot_may_be_saved_disabled(client) -> None:
    routine = _create(client, schedule={"kind": "once", "at": "2026-10-01T08:00"}, enabled=False)
    assert routine["next_run_at"] is None
    response = client.patch(
        f"/api/routines/{routine['id']}", json={"enabled": True}, headers=ALICE_H
    )
    assert response.status_code == 422


def _wait_for_runs(client: TestClient, routine_id: str) -> list[dict]:
    deadline = time.monotonic() + 10
    while True:
        runs = client.get(f"/api/routines/{routine_id}/runs", headers=ALICE_H).json()
        if all(r["status"] != "running" for r in runs):
            return runs
        assert time.monotonic() < deadline
        time.sleep(0.05)


def _wait_for_reply(client: TestClient, thread_id: str) -> list[dict]:
    deadline = time.monotonic() + 10
    while client.get(f"/api/threads/{thread_id}/state", headers=ALICE_H).json()["running"]:
        assert time.monotonic() < deadline
        time.sleep(0.05)
    return client.get(f"/api/threads/{thread_id}/messages", headers=ALICE_H).json()


def test_run_now_starts_a_run_thread(client, fake_model: FakeModel, fake_platform) -> None:
    routine = _create(client, space="/spaces/family")
    fake_model.queue(TextTurn("first brief"), TextTurn("second brief"))

    first = client.post(f"/api/routines/{routine['id']}/run", headers=ALICE_H)
    assert first.status_code == 202, first.text
    messages = _wait_for_reply(client, first.json()["thread_id"])
    assert messages[0]["role"] == "user"
    assert "(space /spaces/family)" in messages[0]["content"]
    assert messages[0]["content"].endswith("Summarize my calendar.")
    assert messages[-1]["content"] == "first brief"
    # The run acts as Alice, through a delegation for its own thread.
    assert fake_platform.exchanges[-1] == ("alice-token", first.json()["thread_id"])

    second = client.post(f"/api/routines/{routine['id']}/run", headers=ALICE_H).json()
    _wait_for_reply(client, second["thread_id"])

    runs = _wait_for_runs(client, routine["id"])
    assert [r["thread_id"] for r in runs] == [second["thread_id"], first.json()["thread_id"]]
    assert [(r["trigger"], r["status"]) for r in runs] == [("manual", "succeeded")] * 2
    assert all(r["started_at"] and r["finished_at"] for r in runs)

    filtered = client.get(f"/api/threads?routine_id={routine['id']}", headers=ALICE_H).json()
    assert [t["id"] for t in filtered] == [r["thread_id"] for r in runs]
    assert filtered[0]["title"].startswith("Morning brief · ")
    assert client.get(f"/api/routines/{routine['id']}", headers=ALICE_H).json()["last_run_at"]

    # Deleting the routine keeps its runs as ordinary chats.
    client.delete(f"/api/routines/{routine['id']}", headers=ALICE_H)
    threads = client.get("/api/threads", headers=ALICE_H).json()
    assert {t["id"] for t in threads} >= {r["thread_id"] for r in runs}
    assert all(t["routine_id"] is None for t in threads)


def test_run_now_rechecks_the_space(client, fake_platform) -> None:
    routine = _create(client, space="/spaces/family")
    fake_platform.members["family"][ALICE] = "viewer"
    response = client.post(f"/api/routines/{routine['id']}/run", headers=ALICE_H)
    assert response.status_code == 403
    assert client.get("/api/threads", headers=ALICE_H).json() == []


def _grants(fake_platform: FakePlatform) -> list[tuple[str, str, str]]:
    return [(g.user_id, g.routine_id, g.space) for g in fake_platform.routine_grants.values()]


def test_an_enabled_routine_holds_one_grant(client, fake_platform) -> None:
    routine = _create(client)
    path = f"/api/routines/{routine['id']}"
    assert "grant_token" not in routine
    assert _grants(fake_platform) == [(ALICE, routine["id"], "/personal")]
    (held,) = fake_platform.routine_grants.values()
    assert held.label == "Routine: Morning brief"

    client.patch(path, json={"enabled": False}, headers=ALICE_H)
    assert _grants(fake_platform) == []
    client.patch(path, json={"enabled": True}, headers=ALICE_H)
    assert _grants(fake_platform) == [(ALICE, routine["id"], "/personal")]
    client.patch(path, json={"space": "/spaces/family"}, headers=ALICE_H)
    assert _grants(fake_platform) == [(ALICE, routine["id"], "/spaces/family")]
    client.patch(path, json={"name": "Renamed"}, headers=ALICE_H)
    assert len(fake_platform.routine_grants) == 1

    assert client.delete(path, headers=ALICE_H).status_code == 204
    assert _grants(fake_platform) == []


def test_a_disabled_routine_has_no_grant(client, fake_platform) -> None:
    routine = _create(client, enabled=False)
    assert fake_platform.routine_grants == {}
    assert client.delete(f"/api/routines/{routine['id']}", headers=ALICE_H).status_code == 204


def test_create_fails_cleanly_without_the_platform(client, fake_platform) -> None:
    fake_platform.unavailable = True
    response = client.post("/api/routines", json=_routine(), headers=ALICE_H)
    assert response.status_code == 503
    fake_platform.unavailable = False
    assert client.get("/api/routines", headers=ALICE_H).json() == []


def _record(client: TestClient, routine_id: str):
    return client.portal.call(client.app.state.routine_store.get, routine_id, ALICE)


def _scheduled_run(client: TestClient, routine_id: str):
    """What the scheduler (M17-04) does for a due routine."""
    state = client.app.state

    async def run():
        routine = await state.routine_store.get(routine_id, ALICE)
        thread = await runs.create_run_thread(state, routine)
        delegation = await runs.grant_delegation(state, routine, thread.id)
        if delegation is None:
            await state.thread_store.delete(thread.id, ALICE)
            return None
        await runs.launch_run(state, routine, thread, delegation)
        return thread.id

    return client.portal.call(run)


def test_a_scheduled_run_acts_through_the_grant(client, fake_model, fake_platform) -> None:
    routine = _create(client, space="/spaces/family")
    (grant,) = fake_platform.routine_grants
    fake_model.queue(TextTurn("unattended brief"))

    thread_id = _scheduled_run(client, routine["id"])
    assert thread_id is not None
    assert _wait_for_reply(client, thread_id)[-1]["content"] == "unattended brief"
    assert fake_platform.grant_exchanges == [(grant, thread_id)]
    assert fake_platform.exchanges == []


def test_a_dead_grant_disables_the_routine(client, fake_platform) -> None:
    routine = _create(client)
    (held,) = fake_platform.routine_grants.values()
    fake_platform.revoked_sessions.add(held.session_id)  # e.g. revoked in Settings

    assert _scheduled_run(client, routine["id"]) is None
    record = _record(client, routine["id"])
    assert (record.enabled, record.next_run_at, record.grant_token) == (False, None, None)
    assert client.get("/api/threads", headers=ALICE_H).json() == []

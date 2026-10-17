"""Delivered routines (M21-07, docs/MEMORY.md §4) with a test definition."""

# ruff: noqa: F811 - the scheduler tests' fixtures, imported

from __future__ import annotations

import re
import time
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from app.agent.approvals import DEFAULT_POLICY, NAMES, ToolPolicy
from app.db.routines import InMemoryRoutineStore, NewRoutine
from app.routines import delivered
from app.routines.delivered import Definition, DeliveredRoutines
from app.routines.runs import RUN_MARKER
from tests.fake_model.scripting import ErrorTurn, FakeModel, TextTurn, ToolCallTurn
from tests.fake_platform.scripting import AGENT_TOKEN, FakePlatform
from tests.test_chat_ws import _drain_turn
from tests.test_hitl_approvals import _bound_tools
from tests.test_integration_tools import HOME, _system
from tests.test_routine_scheduler import (  # noqa: F401
    ALICE,
    HEADERS,
    NOW,
    _fixed_now,
    _routine,
    _runs,
    client,
    harness,
    tool_policies,
)

OFF = frozenset(
    {
        "web_search",
        "web_fetch",
        "open_integration",
        "use_integration",
        "read_integration_resource",
        "get_integration_prompt",
    }
)
TIDY = Definition(
    key="tidy",
    name="Tidy",
    description="Tidies up.",
    time="03:00",
    fixed_prompt="Look at the chats from {since} to {now}.",
    default_prompt="Keep what matters.",
    unavailable_tools=OFF,
    quiet=True,
)
ZONE = "America/Los_Angeles"


@pytest.fixture(autouse=True)
def _tidy(monkeypatch) -> None:
    monkeypatch.setattr(delivered, "DEFINITIONS", {"tidy": TIDY})
    monkeypatch.setattr(delivered, "_now", lambda: NOW)


def _call(client, fn, *args):
    return client.portal.call(fn, *args)


def _set_timezone(client, zone: str = ZONE) -> None:
    _call(client, client.app.state.settings_store.update_document, ALICE, {"timezone": zone})


def _provisioned(client) -> dict:
    _set_timezone(client)
    (routine,) = client.get("/api/routines", headers=HEADERS).json()
    return routine


def _record(client, routine_id: str):
    return _call(client, client.app.state.routine_store.get, routine_id, ALICE)


def _wait_until(check, timeout: float = 10) -> None:
    deadline = time.monotonic() + timeout
    while not check():
        assert time.monotonic() < deadline
        time.sleep(0.05)


def _first_message(client, thread_id: str) -> str:
    messages = client.get(f"/api/threads/{thread_id}/messages", headers=HEADERS).json()
    return messages[0]["content"]


def _run_scheduled(client, harness, routine: dict, *, at: datetime | None = None) -> dict:
    due = at or datetime.fromisoformat(_routine(client, routine["id"])["next_run_at"])
    h = harness(due + timedelta(seconds=5))
    h.start()
    h.tick()
    h.idle()
    return _runs(client, routine["id"])[0]


# --- creating each user's copy -------------------------------------------------------------


def test_a_users_copy_is_created_on_their_first_request_once_the_timezone_is_known(
    client, fake_platform: FakePlatform
) -> None:
    assert client.get("/api/routines", headers=HEADERS).json() == []  # no timezone yet
    _set_timezone(client)
    assert client.get("/api/threads", headers=HEADERS).status_code == 200

    (routine,) = client.get("/api/routines", headers=HEADERS).json()
    offset = delivered.offset_minutes(ALICE)
    assert routine["name"] == "Tidy" and routine["space"] == "/personal"
    assert routine["schedule"]["kind"] == "daily"
    assert routine["schedule"]["time"].startswith(f"{3 + offset // 60:02d}:{offset % 60:02d}")
    assert routine["timezone"] == ZONE and routine["enabled"]
    assert routine["delivered"] and routine["prompt_is_default"]
    assert routine["prompt"] == "Keep what matters."
    assert routine["fixed_prompt"] == TIDY.fixed_prompt
    assert [t["name"] for t in routine["unavailable_tools"]] == [
        "web_search",
        "web_fetch",
        "open_integration",
        "use_integration",
        "read_integration_resource",
        "get_integration_prompt",
    ]
    assert routine["unavailable_tools"][0] == {
        "name": "web_search",
        "label": "Search the web",
        "group": "Web",
    }
    (grant,) = fake_platform.routine_grants.values()
    assert grant.routine_id == routine["id"]


def test_it_is_checked_once_per_user_per_process_and_never_duplicated(
    client, fake_platform: FakePlatform
) -> None:
    routine = _provisioned(client)
    store = client.app.state.routine_store
    _call(client, store.update, routine["id"], ALICE, {"name": "renamed in the database"})
    client.get("/api/threads", headers=HEADERS)
    assert _record(client, routine["id"]).name == "renamed in the database"  # not looked at again

    # A restart forgets that; the copy is found, not made again, and keeps its grant.
    grants = dict(fake_platform.routine_grants)
    client.app.state.delivered_routines = DeliveredRoutines(client.app.state)
    client.get("/api/routines", headers=HEADERS)
    assert [r.id for r in _call(client, store.list_for_owner, ALICE)] == [routine["id"]]
    assert fake_platform.routine_grants == grants


async def test_two_copies_of_one_definition_are_refused_by_the_store() -> None:
    store = InMemoryRoutineStore()
    schedule = {"kind": "daily", "time": "03:00"}
    new = NewRoutine("/personal", "Tidy", "", schedule, "UTC", system_key="tidy")
    await store.create(ALICE, new)
    with pytest.raises(ValueError):
        await store.create(ALICE, new)


def test_the_offset_is_stable_per_user_and_spread_across_users() -> None:
    assert delivered.offset_minutes(ALICE) == delivered.offset_minutes(ALICE)
    offsets = {delivered.offset_minutes(f"user-{n}") for n in range(50)}
    assert all(0 <= o < 120 for o in offsets) and len(offsets) > 20
    assert delivered.default_schedule(TIDY, ALICE).kind == "daily"


# --- rules ---------------------------------------------------------------------------------


def test_it_cant_be_deleted_renamed_or_moved(client) -> None:
    routine = _provisioned(client)
    path = f"/api/routines/{routine['id']}"

    deleted = client.delete(path, headers=HEADERS)
    assert (deleted.status_code, deleted.json()["detail"]) == (409, "delivered_routine")
    assert client.patch(path, json={"name": "Other"}, headers=HEADERS).status_code == 422
    moved = client.patch(path, json={"space": "/spaces/family"}, headers=HEADERS)
    assert moved.status_code == 422
    assert client.patch(path, json={"name": "Tidy"}, headers=HEADERS).status_code == 200


def test_the_editable_part_is_saved_reset_and_defaults_when_empty(client) -> None:
    routine = _provisioned(client)
    path = f"/api/routines/{routine['id']}"

    edited = client.patch(path, json={"prompt": "Keep recipes too."}, headers=HEADERS).json()
    assert (edited["prompt"], edited["prompt_is_default"]) == ("Keep recipes too.", False)
    assert _record(client, routine["id"]).prompt == "Keep recipes too."

    reset = client.patch(path, json={"prompt": ""}, headers=HEADERS).json()
    assert (reset["prompt"], reset["prompt_is_default"]) == ("Keep what matters.", True)
    assert _record(client, routine["id"]).prompt == ""

    # Saving the default's own text keeps it following the default.
    client.patch(path, json={"prompt": "Keep what matters."}, headers=HEADERS)
    assert _record(client, routine["id"]).prompt == ""


def test_on_off_schedule_and_timezone_stay_the_users(client, fake_platform) -> None:
    routine = _provisioned(client)
    path = f"/api/routines/{routine['id']}"
    off = client.patch(path, json={"enabled": False}, headers=HEADERS).json()
    assert not off["enabled"] and off["next_run_at"] is None
    assert fake_platform.routine_grants == {}
    moved = client.patch(
        path,
        json={"enabled": True, "schedule": {"kind": "daily", "time": "05:00"}, "timezone": "UTC"},
        headers=HEADERS,
    ).json()
    assert moved["enabled"] and moved["next_run_at"] == "2026-10-06T05:00:00Z"


def test_an_ordinary_routines_prompt_cant_be_emptied(client) -> None:
    body = {
        "name": "Brief",
        "prompt": "Summarize.",
        "schedule": {"kind": "daily", "time": "08:00"},
        "timezone": ZONE,
    }
    routine = client.post("/api/routines", json=body, headers=HEADERS).json()
    emptied = client.patch(f"/api/routines/{routine['id']}", json={"prompt": ""}, headers=HEADERS)
    assert emptied.status_code == 422
    assert not routine.get("delivered")


def _tool_results(request: dict) -> list[str]:
    return [m["content"] for m in request["messages"] if m.get("role") == "tool"]


def test_the_agent_tools_follow_the_same_rules(
    client, fake_model: FakeModel, tool_policies
) -> None:
    routine = _provisioned(client)
    tool_policies.allow("update_routine", "delete_routine")
    fake_model.queue(
        ToolCallTurn(name="delete_routine", args={"routine": "Tidy"}),
        ToolCallTurn(name="update_routine", args={"routine": "Tidy", "name": "Other"}),
        ToolCallTurn(name="update_routine", args={"routine": "Tidy", "space": "/spaces/x"}),
        ToolCallTurn(name="update_routine", args={"routine": "Tidy", "prompt": "Keep songs."}),
        ToolCallTurn(name="update_routine", args={"routine": "Tidy", "prompt": ""}),
        ToolCallTurn(name="list_routines", args={}),
        TextTurn("done"),
    )
    thread = client.post("/api/threads", json={}, headers=HEADERS).json()["id"]
    with client.websocket_connect(f"/ws/chat/{thread}", headers=HEADERS) as ws:
        ws.send_json({"type": "user_message", "content": "tidy up my routines"})
        frames = _drain_turn(ws)
    assert frames[-1]["type"] == "turn_end"
    assert not [f for f in frames if f["type"] == "approval_request"]

    deleted, renamed, moved, edited, reset, listed = _tool_results(fake_model.requests[-1])
    assert "can't be deleted" in deleted and "turned off instead" in deleted
    assert "can't be renamed" in renamed
    assert "always works in /personal" in moved
    assert edited.startswith("Updated.") and "Keep songs." in edited
    assert reset.startswith("Updated.") and "Keep what matters." in reset
    assert "Delivered with HomeAI" in listed
    assert _record(client, routine["id"]).name == "Tidy"


# --- a run ---------------------------------------------------------------------------------


def test_the_run_message_is_the_framing_the_fixed_part_then_the_editable_part(
    client, harness, fake_model: FakeModel
) -> None:
    routine = _provisioned(client)
    fake_model.queue(TextTurn("tidied"))
    run = _run_scheduled(client, harness, routine)
    assert run["status"] == "succeeded"

    message = _first_message(client, run["thread_id"])
    assert message.startswith(f'{RUN_MARKER} "Tidy" (space /personal)')
    filled = re.search(
        r"\n\nLook at the chats from (\S+) to (\S+)\.\n\nKeep what matters\.$", message
    )
    assert filled, message
    since, now = map(datetime.fromisoformat, filled.groups())
    assert now - since == timedelta(days=14)
    assert now.utcoffset() == timedelta(hours=-7)  # the routine's timezone


async def test_since_is_the_last_successful_runs_start() -> None:
    store = InMemoryRoutineStore()
    state = SimpleNamespace(routine_store=store)
    routine = await store.create(
        ALICE,
        NewRoutine("/personal", "Tidy", "", {"kind": "daily", "time": "03:00"}, "UTC", True),
    )
    now = datetime(2026, 10, 10, 3, 0, tzinfo=UTC)
    assert await delivered.since(state, routine, now) == now - timedelta(days=14)

    first = datetime(2026, 10, 8, 3, 0, tzinfo=UTC)
    ok = await store.create_run(routine, "schedule", "running", started_at=first)
    await store.update_run(ok.id, ALICE, {"status": "succeeded"})
    assert await delivered.since(state, routine, now) == first

    bad = await store.create_run(
        routine, "schedule", "running", started_at=datetime(2026, 10, 9, 3, 0, tzinfo=UTC)
    )
    await store.update_run(bad.id, ALICE, {"status": "failed"})
    assert await delivered.since(state, routine, now) == first  # a failure doesn't move it


def test_a_scheduled_run_doesnt_get_the_unavailable_tools(
    client, harness, fake_model: FakeModel, fake_platform: FakePlatform, tool_policies
) -> None:
    fake_platform.integrations = [HOME]
    tool_policies.policy = ToolPolicy.allow_all()
    routine = _provisioned(client)
    fake_model.queue(
        ToolCallTurn(name="web_search", args={"query": "news"}),
        ToolCallTurn(
            name="task", args={"description": "look it up", "subagent_type": "general-purpose"}
        ),
        ToolCallTurn(name="web_fetch", args={"url": "https://example.com"}),
        TextTurn("the helper couldn't"),
        TextTurn("couldn't"),
    )
    run = _run_scheduled(client, harness, routine)
    assert run["status"] == "succeeded"

    first, _, helper, _, last = fake_model.requests
    assert OFF.isdisjoint(_bound_tools(first)) and "write_file" in _bound_tools(first)
    assert "## Integrations" not in _system(first)
    assert "turned off" in _tool_results(fake_model.requests[1])[-1]
    assert OFF.isdisjoint(_bound_tools(helper))
    assert "turned off" in _tool_results(fake_model.requests[3])[-1]
    assert last["messages"][-1]["role"] == "tool"  # the helper's answer


def test_run_now_and_a_reply_in_the_run_chat_dont_get_them_either(
    client, fake_model: FakeModel, tool_policies
) -> None:
    tool_policies.policy = ToolPolicy.allow_all()
    routine = _provisioned(client)
    fake_model.queue(TextTurn("tidied"))
    started = client.post(f"/api/routines/{routine['id']}/run", headers=HEADERS)
    assert started.status_code == 202, started.text
    thread = started.json()["thread_id"]
    _wait_until(lambda: _runs(client, routine["id"])[0]["status"] == "succeeded")
    assert OFF.isdisjoint(_bound_tools(fake_model.requests[0]))

    fake_model.queue(ToolCallTurn(name="web_fetch", args={"url": "https://x.test"}), TextTurn("no"))
    with client.websocket_connect(f"/ws/chat/{thread}", headers=HEADERS) as ws:
        ws.send_json({"type": "user_message", "content": "fetch it anyway"})
        _drain_turn(ws)
    assert "turned off" in _tool_results(fake_model.requests[-1])[-1]


def test_an_ordinary_routine_keeps_every_tool(client, harness, fake_model, tool_policies) -> None:
    tool_policies.policy = ToolPolicy.allow_all()
    body = {
        "name": "Brief",
        "prompt": "Summarize.",
        "schedule": {"kind": "daily", "time": "08:00"},
        "timezone": ZONE,
    }
    routine = client.post("/api/routines", json=body, headers=HEADERS).json()
    fake_model.queue(TextTurn("brief"))
    _run_scheduled(client, harness, routine)
    assert _bound_tools(fake_model.requests[0]) >= OFF


def test_without_a_policy_every_other_tool_is_bound_and_asks() -> None:
    policy = delivered.without_tools(None, OFF)
    assert policy.available == NAMES - OFF and policy.allowed == frozenset()
    narrowed = delivered.without_tools(DEFAULT_POLICY, OFF)
    assert OFF.isdisjoint(narrowed.available | narrowed.allowed)


# --- a lost grant --------------------------------------------------------------------------


def test_a_lost_grant_skips_runs_keeps_it_on_and_is_replaced_on_the_next_request(
    client, harness, fake_platform: FakePlatform
) -> None:
    routine = _provisioned(client)
    (held,) = fake_platform.routine_grants.values()
    fake_platform.revoked_sessions.add(held.session_id)

    run = _run_scheduled(client, harness, routine)
    assert (run["status"], run["detail"]) == ("missed", delivered.NO_GRANT)
    record = _record(client, routine["id"])
    assert record.enabled and record.grant_token is None and record.next_run_at is not None

    nxt = _run_scheduled(client, harness, routine, at=record.next_run_at)
    assert (nxt["status"], nxt["detail"]) == ("missed", delivered.NO_GRANT)

    client.get("/api/threads", headers=HEADERS)
    record = _record(client, routine["id"])
    assert record.grant_token in fake_platform.routine_grants


# --- quiet runs ----------------------------------------------------------------------------


def test_a_quiet_success_is_neither_unread_nor_listed(client, harness, fake_model) -> None:
    routine = _provisioned(client)
    fake_model.queue(TextTurn("tidied"))
    run = _run_scheduled(client, harness, routine)
    assert run["status"] == "succeeded"

    listed = client.get("/api/threads", headers=HEADERS).json()
    assert run["thread_id"] not in {t["id"] for t in listed}
    (own,) = client.get(f"/api/threads?routine_id={routine['id']}", headers=HEADERS).json()
    assert own["id"] == run["thread_id"] and not own["unread"]


def test_a_quiet_run_waiting_on_approval_is_listed_and_needs_it(
    client, harness, fake_model
) -> None:
    routine = _provisioned(client)
    fake_model.queue(
        ToolCallTurn(name="write_file", args={"file_path": "/personal/Memory/a.md", "content": "x"})
    )
    run = _run_scheduled(client, harness, routine)
    assert run["status"] == "waiting_approval"

    listed = {t["id"]: t for t in client.get("/api/threads", headers=HEADERS).json()}
    assert listed[run["thread_id"]]["needs_approval"]


def test_a_quiet_failure_is_listed_and_unread(client, harness, fake_model) -> None:
    routine = _provisioned(client)
    fake_model.queue(ErrorTurn(500, {"error": {"message": "boom"}}))
    run = _run_scheduled(client, harness, routine)
    assert run["status"] == "failed"
    listed = {t["id"]: t for t in client.get("/api/threads", headers=HEADERS).json()}
    assert listed[run["thread_id"]]["unread"]


# --- the admin's switch (M21-08) -----------------------------------------------------------


@pytest.fixture
def switched(client, fake_platform: FakePlatform):
    """agent-server with a platform token, so it reads the admin's switches."""
    state = client.app.state
    state.settings = state.settings.model_copy(update={"platform_agent_token": AGENT_TOKEN})
    return fake_platform


def test_off_for_everyone_nothing_runs_and_the_users_choice_is_kept(
    client, harness, fake_model: FakeModel, switched
) -> None:
    routine = _provisioned(client)
    assert not routine["disabled_by_admin"]
    switched.delivered_off.add("tidy")

    listed = client.get("/api/routines", headers=HEADERS).json()[0]
    assert listed["disabled_by_admin"] and listed["enabled"]
    assert _routine(client, routine["id"])["disabled_by_admin"]
    due = datetime.fromisoformat(routine["next_run_at"])
    h = harness(due + timedelta(seconds=5))
    h.start()
    h.tick()
    h.idle()
    assert _runs(client, routine["id"]) == []  # skipped quietly: no run record
    assert fake_model.requests == []
    refused = client.post(f"/api/routines/{routine['id']}/run", headers=HEADERS)
    assert (refused.status_code, refused.json()["detail"]) == (409, "disabled_by_admin")

    switched.delivered_off.clear()
    back = _routine(client, routine["id"])
    assert not back["disabled_by_admin"] and back["enabled"]
    fake_model.queue(TextTurn("tidied"))
    run = _run_scheduled(client, harness, routine)
    assert run["status"] == "succeeded"


def test_if_the_platform_cant_say_the_run_fails(client, harness, switched) -> None:
    routine = _provisioned(client)
    switched.delivered_unavailable = True
    run = _run_scheduled(client, harness, routine)
    assert (run["status"], run["detail"]) == ("failed", "couldn't reach the platform")
    assert client.post(f"/api/routines/{routine['id']}/run", headers=HEADERS).status_code == 503


def test_an_ordinary_routine_doesnt_ask_about_switches(
    client, harness, fake_model, switched
) -> None:
    switched.delivered_unavailable = True
    body = {
        "name": "Brief",
        "prompt": "Summarize.",
        "schedule": {"kind": "daily", "time": "08:00"},
        "timezone": ZONE,
    }
    routine = client.post("/api/routines", json=body, headers=HEADERS).json()
    assert routine["disabled_by_admin"] is False
    fake_model.queue(TextTurn("brief"))
    assert _run_scheduled(client, harness, routine)["status"] == "succeeded"


def test_list_routines_tells_the_agent_its_off_for_everyone(
    client, fake_model: FakeModel, switched
) -> None:
    _provisioned(client)
    switched.delivered_off.add("tidy")
    fake_model.queue(ToolCallTurn(name="list_routines", args={}), TextTurn("done"))
    thread = client.post("/api/threads", json={}, headers=HEADERS).json()["id"]
    with client.websocket_connect(f"/ws/chat/{thread}", headers=HEADERS) as ws:
        ws.send_json({"type": "user_message", "content": "my routines?"})
        _drain_turn(ws)
    (listed,) = _tool_results(fake_model.requests[-1])
    assert "admin turned it off for everyone" in listed


async def test_the_definitions_are_registered(fake_platform: FakePlatform, monkeypatch) -> None:
    await delivered.register(fake_platform.base_url, AGENT_TOKEN)
    assert fake_platform.delivered_registered == [
        {"key": "tidy", "name": "Tidy", "description": "Tidies up."}
    ]

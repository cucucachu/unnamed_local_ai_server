"""The agent's routine tools (`app/agent/routine_tools.py`, M17-07) through real chat turns.

A fake model calls the tools over `/ws/chat`; the fake platform checks
spaces and issues routine grants for the user behind the chat's delegation.
"""

from __future__ import annotations

import pytest

from app.agent import routine_tools
from app.agent.routine_tools import ROUTINE_TOOL_NAMES
from app.core.delegation import Caller, DelegationDenied
from tests import test_routine_scheduler as scheduler_tests
from tests.fake_model.scripting import TextTurn, ToolCallTurn
from tests.test_chat_ws import _assert_turn_end, _drain_turn
from tests.test_routine_approvals import _run_once, _tool_results
from tests.test_routine_scheduler import ALICE, HEADERS, NOW, _create

client = scheduler_tests.client
harness = scheduler_tests.harness

DAILY = {
    "name": "Notes digest",
    "prompt": "Summarize what changed in my notes today.",
    "repeat": "weekdays",
    "time": "07:00",
}


@pytest.fixture(autouse=True)
def _tools_now(monkeypatch) -> None:
    monkeypatch.setattr(routine_tools, "_now", lambda: NOW)


def _chat(client, *messages: str) -> tuple[str, list[dict]]:
    thread_id = client.post("/api/threads", json={}, headers=HEADERS).json()["id"]
    with client.websocket_connect(f"/ws/chat/{thread_id}", headers=HEADERS) as ws:
        ws.send_json({"type": "user_message", "content": messages[0]})
        frames = _drain_turn(ws)
    return thread_id, frames


def _answer(client, thread_id: str, approval: dict, decision: str) -> list[dict]:
    with client.websocket_connect(f"/ws/chat/{thread_id}", headers=HEADERS) as ws:
        ws.send_json(
            {
                "type": "approval_response",
                "interrupt_id": approval["interrupt_id"],
                "decisions": [
                    {"tool_call_id": a["tool_call_id"], "decision": decision}
                    for a in approval["actions"]
                ],
            }
        )
        return _drain_turn(ws)


def _approval(frames: list[dict]) -> dict:
    return next(f for f in frames if f["type"] == "approval_request")


def _routines(client) -> list[dict]:
    return client.get("/api/routines", headers=HEADERS).json()


def test_the_agent_has_the_routine_tools(client) -> None:
    names = {t.name for t in routine_tools.make_routine_tools(client.app.state)}
    assert names == set(ROUTINE_TOOL_NAMES)


def test_create_asks_with_the_schedule_in_words(client, fake_model, fake_platform) -> None:
    client.put("/api/settings", json={"timezone": "America/Los_Angeles"}, headers=HEADERS)
    fake_model.queue(ToolCallTurn("create_routine", DAILY), TextTurn("done"))
    thread_id, frames = _chat(client, "every weekday at 7 summarize my notes")
    approval = _approval(frames)
    [action] = approval["actions"]
    assert (action["name"], action["category"]) == ("create_routine", "plan")
    assert (
        'Create the routine "Notes digest": Weekdays at 7:00 (America/Los_Angeles)'
        in (action["description"])
    )
    assert "first run Tue Oct 6, 2026 at 7:00" in action["description"]
    assert "Summarize what changed in my notes today." in action["description"]
    assert _routines(client) == []

    frames = _answer(client, thread_id, approval, "approve")
    _assert_turn_end(frames[-1], "completed")
    [routine] = _routines(client)
    assert routine["schedule"] == {"kind": "weekdays", "time": "07:00:00"}
    assert routine["timezone"] == "America/Los_Angeles"
    assert (routine["space"], routine["approval_mode"], routine["enabled"]) == (
        "/personal",
        "ask",
        True,
    )
    assert routine["next_run_at"] == "2026-10-06T14:00:00Z"
    [grant] = fake_platform.routine_grants.values()
    assert (grant.user_id, grant.routine_id) == (ALICE, routine["id"])
    [out] = _tool_results(fake_model)
    assert out.startswith("Created.") and routine["id"] in out


def test_create_asks_even_with_approvals_off(client, fake_model) -> None:
    client.put("/api/settings", json={"hitl_enabled": False}, headers=HEADERS)
    fake_model.queue(ToolCallTurn("create_routine", DAILY), TextTurn("ok"))
    _, frames = _chat(client, "make a routine")
    assert _approval(frames)["actions"][0]["name"] == "create_routine"


def test_a_rejected_routine_is_not_created(client, fake_model, fake_platform) -> None:
    fake_model.queue(ToolCallTurn("create_routine", DAILY), TextTurn("ok"))
    thread_id, frames = _chat(client, "make a routine")
    fake_model.queue(TextTurn("fine"))
    _answer(client, thread_id, _approval(frames), "reject")
    assert _routines(client) == []
    assert fake_platform.routine_grants == {}
    assert _tool_results(fake_model)[0].startswith("The user rejected this routine")


def test_the_default_timezone_is_utc(client, fake_model) -> None:
    fake_model.queue(ToolCallTurn("create_routine", DAILY), TextTurn("ok"))
    _, frames = _chat(client, "make a routine")
    assert "Weekdays at 7:00 (UTC)" in _approval(frames)["actions"][0]["description"]


@pytest.mark.parametrize(
    ("args", "error"),
    [
        ({"repeat": "weekly"}, "needs `days`"),
        ({"repeat": "monthly"}, "needs `day_of_month`"),
        ({"repeat": "once"}, "needs `date`"),
        ({"repeat": "once", "date": "2026-10-01"}, "the schedule has no future run"),
        ({"time": "25:00"}, "isn't valid"),
        ({"timezone": "Mars/Olympus"}, "unknown timezone"),
        ({"space": "/spaces/family"}, "routines need edit rights on the space"),
        ({"space": "/spaces/nowhere"}, "unknown space"),
    ],
)
def test_bad_routines_are_refused_before_asking(
    client, fake_model, fake_platform, args, error
) -> None:
    fake_platform.add_space("family", {ALICE: "viewer"})
    fake_model.queue(ToolCallTurn("create_routine", DAILY | args), TextTurn("ok"))
    _, frames = _chat(client, "make a routine")
    _assert_turn_end(frames[-1], "completed")
    assert not any(f["type"] == "approval_request" for f in frames)
    [out] = _tool_results(fake_model)
    assert out.startswith("Error:") and error in out
    assert _routines(client) == []


def test_once_weekly_and_monthly_schedules(client, fake_model) -> None:
    cases = [
        ({"repeat": "once", "date": "2026-10-06", "time": "09:00"}, "Once on Oct 6, 2026 at 9:00"),
        ({"repeat": "weekly", "days": ["thu", "mon"]}, "Mon, Thu at 7:00"),
        (
            {"repeat": "monthly", "day_of_month": 31},
            "Monthly on the 31st (or the last day) at 7:00",
        ),
    ]
    for args, words in cases:
        fake_model.queue(ToolCallTurn("create_routine", DAILY | args))
        _, frames = _chat(client, "make a routine")
        assert words in _approval(frames)["actions"][0]["description"]


def test_list_routines(client, fake_model) -> None:
    created = _create(client, name="Morning brief")
    fake_model.queue(ToolCallTurn("list_routines", {}), TextTurn("ok"))
    _chat(client, "what routines do I have?")
    [out] = _tool_results(fake_model)
    assert (
        f'"Morning brief" (id {created["id"]}), on: Weekdays at 8:30 (America/Los_Angeles)' in out
    )
    assert "next run Mon Oct 5, 2026 at 8:30" in out
    assert "Prompt: Summarize my calendar." in out


def test_pausing_needs_no_approval(client, fake_model, fake_platform) -> None:
    created = _create(client)
    assert fake_platform.routine_grants
    args = {"routine": "Morning brief", "enabled": False}
    fake_model.queue(ToolCallTurn("update_routine", args), TextTurn("paused"))
    _, frames = _chat(client, "pause my morning brief")
    _assert_turn_end(frames[-1], "completed")
    assert not any(f["type"] == "approval_request" for f in frames)
    [routine] = _routines(client)
    assert (routine["id"], routine["enabled"], routine["next_run_at"]) == (
        created["id"],
        False,
        None,
    )
    assert fake_platform.routine_grants == {}


def test_changing_the_schedule_asks(client, fake_model) -> None:
    created = _create(client)
    args = {"routine": created["id"], "repeat": "daily", "time": "06:15"}
    fake_model.queue(ToolCallTurn("update_routine", args), TextTurn("ok"))
    thread_id, frames = _chat(client, "make it every day at 6:15")
    description = _approval(frames)["actions"][0]["description"]
    assert 'Change the routine "Morning brief":' in description
    assert "When: Every day at 6:15 (America/Los_Angeles)" in description
    assert _routines(client)[0]["schedule"] == {"kind": "weekdays", "time": "08:30:00"}
    fake_model.queue(TextTurn("changed"))
    _answer(client, thread_id, _approval(frames), "approve")
    assert _routines(client)[0]["schedule"] == {"kind": "daily", "time": "06:15:00"}


def test_a_partial_schedule_change_is_refused(client, fake_model) -> None:
    _create(client)
    fake_model.queue(
        ToolCallTurn("update_routine", {"routine": "morning brief", "time": "06:00"}),
        TextTurn("ok"),
    )
    _, frames = _chat(client, "make it 6")
    assert not any(f["type"] == "approval_request" for f in frames)
    assert "pass `repeat`" in _tool_results(fake_model)[0]


def test_delete_asks_then_deletes(client, fake_model, fake_platform) -> None:
    created = _create(client)
    fake_model.queue(ToolCallTurn("delete_routine", {"routine": created["id"]}), TextTurn("ok"))
    thread_id, frames = _chat(client, "delete my morning brief")
    description = _approval(frames)["actions"][0]["description"]
    assert 'Delete the routine "Morning brief" (Weekdays at 8:30' in description
    fake_model.queue(TextTurn("deleted"))
    _answer(client, thread_id, _approval(frames), "approve")
    assert _routines(client) == []
    assert fake_platform.routine_grants == {}


def test_unknown_routine(client, fake_model) -> None:
    fake_model.queue(ToolCallTurn("delete_routine", {"routine": "nope"}), TextTurn("ok"))
    _chat(client, "delete nope")
    assert 'no routine "nope"' in _tool_results(fake_model)[0]


def test_current_time_is_in_the_users_timezone(client, fake_model) -> None:
    client.put("/api/settings", json={"timezone": "Europe/Paris"}, headers=HEADERS)
    fake_model.queue(ToolCallTurn("current_time", {}), TextTurn("ok"))
    _chat(client, "what time is it?")
    [out] = _tool_results(fake_model)
    assert (
        out == "It's Monday, October 5, 2026, 16:00 (2026-10-05T16:00) in Europe/Paris, UTC+0200."
    )


def test_a_routine_run_cannot_make_routines(client, harness, fake_model) -> None:
    routine, run = _run_once(
        client,
        harness,
        fake_model,
        ToolCallTurn("create_routine", DAILY),
        TextTurn("couldn't"),
        approval_mode="allow_writes",
    )
    assert run["status"] == "succeeded"
    assert _tool_results(fake_model) == [routine_tools._IN_ROUTINE]
    assert [r["id"] for r in _routines(client)] == [routine["id"]]


async def test_the_fake_platform_refuses_a_routine_runs_delegation(fake_platform) -> None:
    grant = fake_platform.issue_routine_grant(
        Caller(identity_token="x"), "r1", "/personal", "Routine: x"
    )
    minted = fake_platform.exchange_routine_grant(grant, "r1", "t1")
    with pytest.raises(DelegationDenied, match="routine_run"):
        fake_platform.space_role(Caller(delegation_token=minted.token), "/personal")

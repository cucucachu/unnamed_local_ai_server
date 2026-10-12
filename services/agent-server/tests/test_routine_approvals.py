"""Routine runs and the user's tool settings (#342), paused runs and their expiry (M17-05);
runs in the chats list (M17-10)."""

from __future__ import annotations

import asyncio
from datetime import timedelta

import pytest

from app.agent import approvals
from app.agent.approvals import DEFAULT_POLICY, ToolPolicy
from app.routines.scheduler import EXPIRED_MESSAGE
from tests import test_routine_scheduler as scheduler_tests
from tests.fake_model.scripting import FakeModel, TextTurn, ToolCallsTurn, ToolCallTurn
from tests.fake_tool_policies import FakeToolPolicies
from tests.test_chat_ws import _drain_turn
from tests.test_routine_scheduler import ALICE, HEADERS, _create, _runs

client = scheduler_tests.client
harness = scheduler_tests.harness
_fixed_now = scheduler_tests._fixed_now
tool_policies = scheduler_tests.tool_policies

WRITE = ("write_file", {"file_path": "/personal/brief.md", "content": "today"})
DELETE = ("delete", {"file_path": "/personal/old.md"})


def _run_once(client, harness, fake_model: FakeModel, *turns, **routine) -> tuple[dict, dict]:
    created = _create(client, **routine)
    fake_model.queue(*turns)
    h = harness()
    h.start()
    h.tick()
    h.idle()
    (run,) = _runs(client, created["id"])
    return created, run


def _pending(client, thread_id: str) -> dict | None:
    state = client.get(f"/api/threads/{thread_id}/state", headers=HEADERS).json()
    return state["pending_approval"]


def _tool_results(fake_model: FakeModel) -> list[str]:
    return [
        str(m.get("content"))
        for m in fake_model.requests[-1]["messages"]
        if m.get("role") == "tool"
    ]


def _wait_for_status(client, routine_id: str, status: str) -> dict:
    store = client.app.state.routine_store

    async def settled():
        while (await store.list_runs(routine_id, ALICE))[0].status != status:
            await asyncio.sleep(0.01)

    client.portal.call(lambda: asyncio.wait_for(settled(), 10))
    return _runs(client, routine_id)[0]


def test_a_routine_asks_by_default(client, harness, fake_model, fake_platform) -> None:
    routine, run = _run_once(client, harness, fake_model, ToolCallTurn(*WRITE))
    assert "approval_mode" not in routine
    assert run["status"] == "waiting_approval"
    assert [a["name"] for a in _pending(client, run["thread_id"])["actions"]] == ["write_file"]
    assert fake_platform.personal(ALICE) == {}
    (thread,) = client.get("/api/threads", headers=HEADERS).json()
    assert (thread["id"], thread["needs_approval"]) == (run["thread_id"], True)


def test_a_run_uses_the_users_tool_settings(
    client, harness, fake_model, fake_platform, tool_policies: FakeToolPolicies
) -> None:
    tool_policies.allow("write_file")
    _, run = _run_once(client, harness, fake_model, ToolCallTurn(*WRITE), TextTurn("written"))
    assert run["status"] == "succeeded"
    assert fake_platform.personal(ALICE) == {"brief.md": b"today"}


def _approve_all(pending: dict) -> dict:
    return {
        "type": "approval_response",
        "interrupt_id": pending["interrupt_id"],
        "decisions": [
            {"tool_call_id": a["tool_call_id"], "decision": "approve"} for a in pending["actions"]
        ],
    }


def test_a_run_asks_only_for_what_isnt_allowed_and_resumes(
    client, harness, fake_model, fake_platform, tool_policies: FakeToolPolicies
) -> None:
    fake_platform.personal(ALICE)["old.md"] = b"stale"
    tool_policies.allow("write_file")
    routine, run = _run_once(client, harness, fake_model, ToolCallsTurn([WRITE, DELETE]))
    assert run["status"] == "waiting_approval"
    pending = _pending(client, run["thread_id"])
    (action,) = pending["actions"]
    assert action["name"] == "delete"
    assert "can_allow_writes" not in pending
    assert fake_platform.personal(ALICE) == {"old.md": b"stale"}  # nothing ran yet

    fake_model.queue(TextTurn("tidied"))
    with client.websocket_connect(f"/ws/chat/{run['thread_id']}", headers=HEADERS) as ws:
        ws.send_json(_approve_all(pending))
        frames = _drain_turn(ws)
    assert frames[-1]["status"] == "completed"
    assert fake_platform.personal(ALICE) == {"brief.md": b"today"}
    finished = _wait_for_status(client, routine["id"], "succeeded")
    assert finished["detail"] is None
    assert client.get("/api/threads", headers=HEADERS).json()[0]["needs_approval"] is False


def test_always_allow_on_a_paused_run_finishes_it_unasked(
    client, harness, fake_model, fake_platform, tool_policies: FakeToolPolicies
) -> None:
    routine, run = _run_once(client, harness, fake_model, ToolCallTurn(*WRITE))
    pending = _pending(client, run["thread_id"])

    tool_policies.allow("write_file")
    fake_model.queue(
        ToolCallTurn("write_file", {"file_path": "/personal/second.md", "content": "more"}),
        TextTurn("both written"),
    )
    with client.websocket_connect(f"/ws/chat/{run['thread_id']}", headers=HEADERS) as ws:
        # An app from before #342 may still send `allow_writes`; it's ignored.
        ws.send_json(_approve_all(pending) | {"allow_writes": True})
        frames = _drain_turn(ws)
    assert frames[-1]["status"] == "completed"
    assert not any(f["type"] == "approval_request" for f in frames)
    assert fake_platform.personal(ALICE) == {"brief.md": b"today", "second.md": b"more"}
    assert _wait_for_status(client, routine["id"], "succeeded")["status"] == "succeeded"


def test_a_turned_off_tool_is_refused_in_a_run(
    client, harness, fake_model, fake_platform, tool_policies: FakeToolPolicies
) -> None:
    tool_policies.turn_off("write_file")
    _, run = _run_once(
        client, harness, fake_model, ToolCallTurn(*WRITE), TextTurn("couldn't write")
    )
    assert run["status"] == "succeeded"
    assert fake_platform.personal(ALICE) == {}
    (result,) = _tool_results(fake_model)
    assert "turned off" in result
    tools = {t["function"]["name"] for t in fake_model.requests[0].get("tools") or []}
    assert "write_file" not in tools


def test_an_unanswered_approval_expires_rejected(
    client, harness, fake_model, fake_platform
) -> None:
    routine = _create(client)
    fake_model.queue(ToolCallTurn(*WRITE))
    h = harness(approval_ttl=timedelta(hours=24))
    h.start()
    h.tick()
    h.idle()
    assert _runs(client, routine["id"])[0]["status"] == "waiting_approval"

    h.clock.now += timedelta(hours=23)
    h.call(h.scheduler.expire_stale)
    assert _runs(client, routine["id"])[0]["status"] == "waiting_approval"

    fake_model.queue(TextTurn("ok, left it"))
    h.clock.now += timedelta(hours=2)
    h.call(h.scheduler.expire_stale)
    run = _wait_for_status(client, routine["id"], "expired")
    assert "nobody answered" in run["detail"]
    assert fake_platform.personal(ALICE) == {}
    assert EXPIRED_MESSAGE in _tool_results(fake_model)[0]
    assert _pending(client, run["thread_id"]) is None


def test_a_routine_run_is_an_unread_chat_until_opened(client, harness, fake_model) -> None:
    """M17-10: nobody watched it, so its chat is unread; a paused one comes first."""
    _, done = _run_once(client, harness, fake_model, TextTurn("done"))
    _, paused = _run_once(client, harness, fake_model, ToolCallTurn(*WRITE), name="Writer")
    listed = client.get("/api/threads", headers=HEADERS).json()
    assert [(t["id"], t["needs_approval"], t["unread"]) for t in listed] == [
        (paused["thread_id"], True, True),
        (done["thread_id"], False, True),
    ]

    read = client.post(f"/api/threads/{done['thread_id']}/read", headers=HEADERS)
    assert read.status_code == 204
    listed = client.get("/api/threads", headers=HEADERS).json()
    assert [t["unread"] for t in listed] == [True, False]


def test_approval_mode_from_an_older_app_is_ignored(client) -> None:
    routine = _create(client, approval_mode="read_only")
    assert "approval_mode" not in routine
    url = f"/api/routines/{routine['id']}"
    changed = client.patch(url, json={"approval_mode": "allow_writes"}, headers=HEADERS)
    assert changed.status_code == 200
    assert "approval_mode" not in changed.json()


ALLOWS_WRITES = ToolPolicy(DEFAULT_POLICY.available, DEFAULT_POLICY.allowed | {"write_file"})


@pytest.mark.parametrize(
    ("configurable", "tool", "asks"),
    [
        ({}, "ls", True),
        ({"tool_policy": DEFAULT_POLICY}, "ls", False),
        ({"tool_policy": DEFAULT_POLICY}, "write_file", True),
        ({"tool_policy": DEFAULT_POLICY}, "execute_code", True),
        ({"tool_policy": ALLOWS_WRITES}, "write_file", False),
        ({"tool_policy": ALLOWS_WRITES, "pinned_approvals": {"c1": True}}, "write_file", True),
        ({"tool_policy": DEFAULT_POLICY, "pinned_approvals": {"c1": False}}, "write_file", False),
        ({"tool_policy": DEFAULT_POLICY, "pinned_approvals": {"c2": False}}, "write_file", True),
        ({"tool_policy": ToolPolicy.allow_all()}, "approve_migration", False),
    ],
)
def test_which_calls_need_approval(configurable, tool, asks) -> None:
    assert approvals.needs_approval({"configurable": configurable}, tool, "c1") is asks

"""Routine approval modes, paused runs and their expiry (M17-05); runs in the chats list (M17-10)."""

from __future__ import annotations

import asyncio
from datetime import timedelta

import pytest

from app.agent import approvals
from app.routines.scheduler import EXPIRED_MESSAGE
from tests import test_routine_scheduler as scheduler_tests
from tests.fake_model.scripting import FakeModel, TextTurn, ToolCallsTurn, ToolCallTurn
from tests.test_chat_ws import _drain_turn
from tests.test_routine_scheduler import ALICE, HEADERS, _create, _runs

client = scheduler_tests.client
harness = scheduler_tests.harness
_fixed_now = scheduler_tests._fixed_now

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
    assert routine["approval_mode"] == "ask"
    assert run["status"] == "waiting_approval"
    assert [a["name"] for a in _pending(client, run["thread_id"])["actions"]] == ["write_file"]
    assert fake_platform.personal(ALICE) == {}
    (thread,) = client.get("/api/threads", headers=HEADERS).json()
    assert (thread["id"], thread["needs_approval"]) == (run["thread_id"], True)


def test_ask_still_asks_with_approvals_turned_off_in_settings(client, harness, fake_model) -> None:
    client.put("/api/settings", json={"hitl_enabled": False}, headers=HEADERS)
    _, run = _run_once(client, harness, fake_model, ToolCallTurn(*WRITE))
    assert run["status"] == "waiting_approval"


def test_allow_writes_runs_writes_unasked(client, harness, fake_model, fake_platform) -> None:
    _, run = _run_once(
        client,
        harness,
        fake_model,
        ToolCallTurn(*WRITE),
        TextTurn("written"),
        approval_mode="allow_writes",
    )
    assert run["status"] == "succeeded"
    assert fake_platform.personal(ALICE) == {"brief.md": b"today"}


def test_allow_writes_still_asks_before_a_delete_and_resumes_in_that_mode(
    client, harness, fake_model, fake_platform
) -> None:
    fake_platform.personal(ALICE)["old.md"] = b"stale"
    routine, run = _run_once(
        client, harness, fake_model, ToolCallsTurn([WRITE, DELETE]), approval_mode="allow_writes"
    )
    assert run["status"] == "waiting_approval"
    pending = _pending(client, run["thread_id"])
    (action,) = pending["actions"]
    assert action["name"] == "delete"
    assert fake_platform.personal(ALICE) == {"old.md": b"stale"}  # nothing ran yet

    fake_model.queue(TextTurn("tidied"))
    with client.websocket_connect(f"/ws/chat/{run['thread_id']}", headers=HEADERS) as ws:
        ws.send_json(
            {
                "type": "approval_response",
                "interrupt_id": pending["interrupt_id"],
                "decisions": [{"tool_call_id": action["tool_call_id"], "decision": "approve"}],
            }
        )
        frames = _drain_turn(ws)
    assert frames[-1]["status"] == "completed"
    assert fake_platform.personal(ALICE) == {"brief.md": b"today"}
    finished = _wait_for_status(client, routine["id"], "succeeded")
    assert finished["detail"] is None
    assert client.get("/api/threads", headers=HEADERS).json()[0]["needs_approval"] is False


def _approve_all(pending: dict, **extra) -> dict:
    return {
        "type": "approval_response",
        "interrupt_id": pending["interrupt_id"],
        "decisions": [
            {"tool_call_id": a["tool_call_id"], "decision": "approve"} for a in pending["actions"]
        ],
        **extra,
    }


def test_approving_with_allow_writes_switches_the_routine_and_finishes_the_run(
    client, harness, fake_model, fake_platform
) -> None:
    routine, run = _run_once(client, harness, fake_model, ToolCallTurn(*WRITE))
    pending = _pending(client, run["thread_id"])
    assert pending["can_allow_writes"] is True

    fake_model.queue(
        ToolCallTurn("write_file", {"file_path": "/personal/second.md", "content": "more"}),
        TextTurn("both written"),
    )
    with client.websocket_connect(f"/ws/chat/{run['thread_id']}", headers=HEADERS) as ws:
        ws.send_json(_approve_all(pending, allow_writes=True))
        frames = _drain_turn(ws)
    assert frames[-1]["status"] == "completed"
    assert not any(f["type"] == "approval_request" for f in frames)
    assert fake_platform.personal(ALICE) == {"brief.md": b"today", "second.md": b"more"}
    assert _wait_for_status(client, routine["id"], "succeeded")["status"] == "succeeded"
    updated = client.get(f"/api/routines/{routine['id']}", headers=HEADERS).json()
    assert updated["approval_mode"] == "allow_writes"


def test_allow_writes_is_not_offered_for_a_delete_or_outside_ask_mode(
    client, harness, fake_model, fake_platform
) -> None:
    fake_platform.personal(ALICE)["old.md"] = b"stale"
    _, ask_run = _run_once(client, harness, fake_model, ToolCallTurn(*DELETE))
    assert "can_allow_writes" not in _pending(client, ask_run["thread_id"])

    _, writes_run = _run_once(
        client, harness, fake_model, ToolCallTurn(*DELETE), approval_mode="allow_writes"
    )
    assert "can_allow_writes" not in _pending(client, writes_run["thread_id"])


@pytest.mark.parametrize("case", ["delete pending", "rejected"])
def test_allow_writes_is_refused_where_it_was_not_offered(
    case, client, harness, fake_model, fake_platform
) -> None:
    fake_platform.personal(ALICE)["old.md"] = b"stale"
    turn = ToolCallTurn(*DELETE) if case == "delete pending" else ToolCallTurn(*WRITE)
    routine, run = _run_once(client, harness, fake_model, turn)
    pending = _pending(client, run["thread_id"])
    frame = _approve_all(pending, allow_writes=True)
    if case == "rejected":
        frame["decisions"][0]["decision"] = "reject"
    with client.websocket_connect(f"/ws/chat/{run['thread_id']}", headers=HEADERS) as ws:
        ws.send_json(frame)
        error = ws.receive_json()
    assert error["type"] == "error" and "allow_writes" in error["message"]
    unchanged = client.get(f"/api/routines/{routine['id']}", headers=HEADERS).json()
    assert unchanged["approval_mode"] == "ask"
    assert _pending(client, run["thread_id"]) is not None


def test_read_only_refuses_writes(client, harness, fake_model, fake_platform) -> None:
    _, run = _run_once(
        client,
        harness,
        fake_model,
        ToolCallTurn(*WRITE),
        TextTurn("couldn't write"),
        approval_mode="read_only",
    )
    assert run["status"] == "succeeded"
    assert fake_platform.personal(ALICE) == {}
    (result,) = _tool_results(fake_model)
    assert "read-only" in result


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


def test_a_routines_approval_mode_can_be_changed(client) -> None:
    routine = _create(client)
    url = f"/api/routines/{routine['id']}"
    changed = client.patch(url, json={"approval_mode": "read_only"}, headers=HEADERS)
    assert changed.json()["approval_mode"] == "read_only"
    bad = client.patch(url, json={"approval_mode": "anything"}, headers=HEADERS)
    assert bad.status_code == 422


@pytest.mark.parametrize(
    ("config", "tool", "asks"),
    [
        ({}, "write_file", True),
        ({"hitl_enabled": False}, "write_file", False),
        ({"approval_mode": "ask", "hitl_enabled": False}, "write_file", True),
        ({"approval_mode": "allow_writes"}, "write_file", False),
        ({"approval_mode": "allow_writes"}, "execute_code", False),
        ({"approval_mode": "allow_writes"}, "delete", True),
        ({"approval_mode": "allow_writes"}, "approve_migration", True),
    ],
)
def test_which_calls_need_approval(config, tool, asks) -> None:
    assert approvals.needs_approval({"configurable": config}, tool) is asks

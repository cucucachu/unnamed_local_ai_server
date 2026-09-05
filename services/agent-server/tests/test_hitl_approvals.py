"""Tests for M8-03 human-in-the-loop approvals (`interrupt_on`).

Exercises the full `chat_ws.py` + `chat.py` surface added by this ticket:
`approval_request`/`turn_end {"status": "awaiting_approval"}`,
`approval_response` (approve/reject), `cancel`-while-awaiting-approval
(reject-all), HITL-off (no interrupt at all), and `GET
/api/threads/{id}/state` reflecting a pending approval across a simulated
reconnect (a fresh REST call with no WS turn running).

Uses the same `_make_client`/`_drain_turn`/`FakeModel` fixtures as
`test_chat_ws.py` — see that module for the underlying wire-format
assertions (`tool_start`/`tool_end`/`turn_end` shapes) this ticket doesn't
re-test from scratch.
"""

from __future__ import annotations

from app.db.settings import InMemorySettingsStore
from app.db.threads import InMemoryThreadStore
from tests.fake_model.scripting import FakeModel, TextTurn, ToolCallTurn
from tests.test_chat_ws import _drain_turn, _make_client


async def _hitl_settings_store(enabled: bool) -> InMemorySettingsStore:
    store = InMemorySettingsStore()
    await store.update_document({"hitl_enabled": enabled})
    return store


async def test_write_file_with_hitl_on_emits_approval_request(fake_model: FakeModel, tmp_path) -> None:
    fake_model.queue(ToolCallTurn(name="write_file", args={"file_path": "/x.txt", "content": "y"}))

    with _make_client(
        fake_model, tmp_path, settings_store=await _hitl_settings_store(True)
    ) as client, client.websocket_connect("/ws/chat/hitl-on-thread") as ws:
        ws.send_json({"type": "user_message", "content": "write a file"})
        frames = _drain_turn(ws)

    assert frames[0] == {"type": "turn_start"}
    assert frames[-1] == {"type": "turn_end", "status": "awaiting_approval"}

    approval_frames = [f for f in frames if f["type"] == "approval_request"]
    assert len(approval_frames) == 1
    approval = approval_frames[0]
    assert isinstance(approval["interrupt_id"], str) and approval["interrupt_id"]
    assert len(approval["actions"]) == 1
    action = approval["actions"][0]
    assert action["name"] == "write_file"
    assert action["category"] == "file"
    assert action["args"] == {"file_path": "/x.txt", "content": "y"}
    assert isinstance(action["tool_call_id"], str) and action["tool_call_id"]
    assert isinstance(action["description"], str) and action["description"]

    # No tool_start/tool_end for the paused call — it hasn't executed yet.
    assert not any(f["type"] in ("tool_start", "tool_end") for f in frames)

    written = tmp_path / "x.txt"
    assert not written.exists()


async def test_approve_writes_file_and_completes_turn(fake_model: FakeModel, tmp_path) -> None:
    fake_model.queue(ToolCallTurn(name="write_file", args={"file_path": "/x.txt", "content": "y"}))

    with _make_client(
        fake_model, tmp_path, settings_store=await _hitl_settings_store(True)
    ) as client, client.websocket_connect("/ws/chat/hitl-approve-thread") as ws:
        ws.send_json({"type": "user_message", "content": "write a file"})
        frames = _drain_turn(ws)
        approval = next(f for f in frames if f["type"] == "approval_request")
        tool_call_id = approval["actions"][0]["tool_call_id"]

        fake_model.queue(TextTurn("done"))
        ws.send_json(
            {
                "type": "approval_response",
                "interrupt_id": approval["interrupt_id"],
                "decisions": [{"tool_call_id": tool_call_id, "decision": "approve"}],
            }
        )
        resume_frames = _drain_turn(ws)

    assert resume_frames[0] == {"type": "turn_start"}
    assert resume_frames[-1] == {"type": "turn_end", "status": "completed"}

    types = [f["type"] for f in resume_frames]
    assert "tool_start" in types
    assert "tool_end" in types
    tool_end = next(f for f in resume_frames if f["type"] == "tool_end")
    assert tool_end["name"] == "write_file"
    assert tool_end["status"] == "success"

    written = tmp_path / "x.txt"
    assert written.exists()
    assert written.read_text() == "y"

    token_text = "".join(f["content"] for f in resume_frames if f["type"] == "token")
    assert token_text == "done"


async def test_reject_does_not_write_file_and_informs_model(fake_model: FakeModel, tmp_path) -> None:
    fake_model.queue(ToolCallTurn(name="write_file", args={"file_path": "/x.txt", "content": "y"}))

    with _make_client(
        fake_model, tmp_path, settings_store=await _hitl_settings_store(True)
    ) as client, client.websocket_connect("/ws/chat/hitl-reject-thread") as ws:
        ws.send_json({"type": "user_message", "content": "write a file"})
        frames = _drain_turn(ws)
        approval = next(f for f in frames if f["type"] == "approval_request")
        tool_call_id = approval["actions"][0]["tool_call_id"]

        fake_model.queue(TextTurn("okay, I won't write that file"))
        ws.send_json(
            {
                "type": "approval_response",
                "interrupt_id": approval["interrupt_id"],
                "decisions": [{"tool_call_id": tool_call_id, "decision": "reject"}],
            }
        )
        resume_frames = _drain_turn(ws)

    assert resume_frames[0] == {"type": "turn_start"}
    assert resume_frames[-1] == {"type": "turn_end", "status": "completed"}
    # No tool execution frames — the tool call was rejected, never run.
    assert not any(f["type"] in ("tool_start", "tool_end") for f in resume_frames)

    written = tmp_path / "x.txt"
    assert not written.exists()

    # The model's next request carries the rejection as a tool message.
    last_request_messages = fake_model.requests[-1]["messages"]
    tool_messages = [m for m in last_request_messages if m.get("role") == "tool"]
    assert tool_messages
    assert "rejected" in tool_messages[-1]["content"].lower()


async def test_cancel_while_awaiting_approval_rejects_all(fake_model: FakeModel, tmp_path) -> None:
    fake_model.queue(ToolCallTurn(name="write_file", args={"file_path": "/x.txt", "content": "y"}))

    with _make_client(
        fake_model, tmp_path, settings_store=await _hitl_settings_store(True)
    ) as client, client.websocket_connect("/ws/chat/hitl-cancel-thread") as ws:
        ws.send_json({"type": "user_message", "content": "write a file"})
        frames = _drain_turn(ws)
        assert frames[-1] == {"type": "turn_end", "status": "awaiting_approval"}

        fake_model.queue(TextTurn("no problem"))
        ws.send_json({"type": "cancel"})
        resume_frames = _drain_turn(ws)

    assert resume_frames[0] == {"type": "turn_start"}
    assert resume_frames[-1] == {"type": "turn_end", "status": "completed"}

    written = tmp_path / "x.txt"
    assert not written.exists()

    last_request_messages = fake_model.requests[-1]["messages"]
    tool_messages = [m for m in last_request_messages if m.get("role") == "tool"]
    assert tool_messages
    assert "the user cancelled" in tool_messages[-1]["content"].lower()


async def test_hitl_off_no_approval_request_at_all(fake_model: FakeModel, tmp_path) -> None:
    fake_model.queue(
        ToolCallTurn(name="write_file", args={"file_path": "/x.txt", "content": "y"}),
        TextTurn("done"),
    )

    with _make_client(
        fake_model, tmp_path, settings_store=await _hitl_settings_store(False)
    ) as client, client.websocket_connect("/ws/chat/hitl-off-thread") as ws:
        ws.send_json({"type": "user_message", "content": "write a file"})
        frames = _drain_turn(ws)

    assert frames[0] == {"type": "turn_start"}
    assert frames[-1] == {"type": "turn_end", "status": "completed"}
    assert not any(f["type"] == "approval_request" for f in frames)

    written = tmp_path / "x.txt"
    assert written.exists()
    assert written.read_text() == "y"


async def test_thread_state_reflects_pending_approval_across_reconnect(
    fake_model: FakeModel, tmp_path
) -> None:
    """Simulates a reconnect: query `/api/threads/{id}/state` with no WS turn
    running, after a previous turn left an approval pending."""
    fake_model.queue(ToolCallTurn(name="write_file", args={"file_path": "/x.txt", "content": "y"}))
    thread_store = InMemoryThreadStore()

    with _make_client(
        fake_model,
        tmp_path,
        thread_store=thread_store,
        settings_store=await _hitl_settings_store(True),
    ) as client:
        with client.websocket_connect("/ws/chat/hitl-state-thread") as ws:
            ws.send_json({"type": "user_message", "content": "write a file"})
            frames = _drain_turn(ws)
        approval = next(f for f in frames if f["type"] == "approval_request")

        # No WS connection open now — this is the "reconnect" REST call.
        state_response = client.get("/api/threads/hitl-state-thread/state")
        assert state_response.status_code == 200
        body = state_response.json()
        assert body["pending_approval"] is not None
        assert body["pending_approval"]["interrupt_id"] == approval["interrupt_id"]
        assert body["pending_approval"]["actions"] == approval["actions"]


async def test_approval_response_resumes_after_reconnect(fake_model: FakeModel, tmp_path) -> None:
    """A new WS connection must accept `approval_response` for an interrupt
    left pending by a previous connection (the checkpointer is the source
    of truth — see `chat_ws` hydrating `pending_approval` on connect)."""
    fake_model.queue(ToolCallTurn(name="write_file", args={"file_path": "/x.txt", "content": "y"}))
    thread_store = InMemoryThreadStore()

    with _make_client(
        fake_model,
        tmp_path,
        thread_store=thread_store,
        settings_store=await _hitl_settings_store(True),
    ) as client:
        with client.websocket_connect("/ws/chat/hitl-reconnect-thread") as ws:
            ws.send_json({"type": "user_message", "content": "write a file"})
            frames = _drain_turn(ws)
        approval = next(f for f in frames if f["type"] == "approval_request")
        tool_call_id = approval["actions"][0]["tool_call_id"]

        fake_model.queue(TextTurn("done"))
        with client.websocket_connect("/ws/chat/hitl-reconnect-thread") as ws:
            ws.send_json(
                {
                    "type": "approval_response",
                    "interrupt_id": approval["interrupt_id"],
                    "decisions": [{"tool_call_id": tool_call_id, "decision": "approve"}],
                }
            )
            resume_frames = _drain_turn(ws)

    assert resume_frames[0] == {"type": "turn_start"}
    assert resume_frames[-1] == {"type": "turn_end", "status": "completed"}
    written = tmp_path / "x.txt"
    assert written.exists()
    assert written.read_text() == "y"


async def test_thread_state_is_null_when_nothing_pending(fake_model: FakeModel, tmp_path) -> None:
    fake_model.queue(TextTurn("hello"))

    with _make_client(
        fake_model, tmp_path, settings_store=await _hitl_settings_store(True)
    ) as client:
        with client.websocket_connect("/ws/chat/hitl-state-empty-thread") as ws:
            ws.send_json({"type": "user_message", "content": "hi"})
            _drain_turn(ws)

        state_response = client.get("/api/threads/hitl-state-empty-thread/state")
        assert state_response.status_code == 200
        assert state_response.json() == {"pending_approval": None}

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

import asyncio
import json

import anyio
import pytest

from app.db.settings import InMemorySettingsStore
from app.db.turn_stats import InMemoryTurnStatsStore, TurnStat
from tests.fake_identity import TEST_USER_ID, AutoCreateThreadStore
from tests.fake_model.scripting import FakeModel, TextTurn, ToolCallTurn
from tests.fake_platform.scripting import FakePlatform
from tests.test_chat_ws import _assert_turn_end, _drain_turn, _make_client


async def _hitl_settings_store(enabled: bool) -> InMemorySettingsStore:
    store = InMemorySettingsStore()
    await store.update_document(TEST_USER_ID, {"hitl_enabled": enabled})
    return store


async def test_write_file_with_hitl_on_emits_approval_request(fake_model: FakeModel, fake_platform: FakePlatform) -> None:
    fake_model.queue(ToolCallTurn(name="write_file", args={"file_path": "/personal/x.txt", "content": "y"}))

    with _make_client(
        fake_model, fake_platform, settings_store=await _hitl_settings_store(True)
    ) as client, client.websocket_connect("/ws/chat/hitl-on-thread") as ws:
        ws.send_json({"type": "user_message", "content": "write a file"})
        frames = _drain_turn(ws)

    assert frames[0] == {"type": "turn_start"}
    _assert_turn_end(frames[-1], "awaiting_approval")

    approval_frames = [f for f in frames if f["type"] == "approval_request"]
    assert len(approval_frames) == 1
    approval = approval_frames[0]
    assert isinstance(approval["interrupt_id"], str) and approval["interrupt_id"]
    assert len(approval["actions"]) == 1
    action = approval["actions"][0]
    assert action["name"] == "write_file"
    assert action["category"] == "file"
    assert action["args"] == {"file_path": "/personal/x.txt", "content": "y"}
    assert isinstance(action["tool_call_id"], str) and action["tool_call_id"]
    assert action["description"] == "Write file `/personal/x.txt`"

    # No tool_start/tool_end for the paused call — it hasn't executed yet.
    assert not any(f["type"] in ("tool_start", "tool_end") for f in frames)

    assert fake_platform.personal() == {}


async def test_approve_writes_file_and_completes_turn(fake_model: FakeModel, fake_platform: FakePlatform) -> None:
    fake_model.queue(ToolCallTurn(name="write_file", args={"file_path": "/personal/x.txt", "content": "y"}))

    with _make_client(
        fake_model, fake_platform, settings_store=await _hitl_settings_store(True)
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
    _assert_turn_end(resume_frames[-1], "completed")

    types = [f["type"] for f in resume_frames]
    assert "tool_start" in types
    assert "tool_end" in types
    tool_end = next(f for f in resume_frames if f["type"] == "tool_end")
    assert tool_end["name"] == "write_file"
    assert tool_end["status"] == "success"

    assert fake_platform.personal() == {"x.txt": b"y"}

    token_text = "".join(f["content"] for f in resume_frames if f["type"] == "token")
    assert token_text == "done"


async def test_reject_does_not_write_file_and_informs_model(fake_model: FakeModel, fake_platform: FakePlatform) -> None:
    fake_model.queue(ToolCallTurn(name="write_file", args={"file_path": "/personal/x.txt", "content": "y"}))

    with _make_client(
        fake_model, fake_platform, settings_store=await _hitl_settings_store(True)
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
    _assert_turn_end(resume_frames[-1], "completed")
    # No tool execution frames — the tool call was rejected, never run.
    assert not any(f["type"] in ("tool_start", "tool_end") for f in resume_frames)

    assert fake_platform.personal() == {}

    # The model's next request carries the rejection as a tool message.
    last_request_messages = fake_model.requests[-1]["messages"]
    tool_messages = [m for m in last_request_messages if m.get("role") == "tool"]
    assert tool_messages
    assert "rejected" in tool_messages[-1]["content"].lower()


async def test_cancel_while_awaiting_approval_rejects_all(fake_model: FakeModel, fake_platform: FakePlatform) -> None:
    fake_model.queue(ToolCallTurn(name="write_file", args={"file_path": "/personal/x.txt", "content": "y"}))

    with _make_client(
        fake_model, fake_platform, settings_store=await _hitl_settings_store(True)
    ) as client, client.websocket_connect("/ws/chat/hitl-cancel-thread") as ws:
        ws.send_json({"type": "user_message", "content": "write a file"})
        frames = _drain_turn(ws)
        _assert_turn_end(frames[-1], "awaiting_approval")

        fake_model.queue(TextTurn("no problem"))
        ws.send_json({"type": "cancel"})
        resume_frames = _drain_turn(ws)

    assert resume_frames[0] == {"type": "turn_start"}
    _assert_turn_end(resume_frames[-1], "completed")

    assert fake_platform.personal() == {}

    last_request_messages = fake_model.requests[-1]["messages"]
    tool_messages = [m for m in last_request_messages if m.get("role") == "tool"]
    assert tool_messages
    assert "the user cancelled" in tool_messages[-1]["content"].lower()


async def test_hitl_off_no_approval_request_at_all(fake_model: FakeModel, fake_platform: FakePlatform) -> None:
    fake_model.queue(
        ToolCallTurn(name="write_file", args={"file_path": "/personal/x.txt", "content": "y"}),
        TextTurn("done"),
    )

    with _make_client(
        fake_model, fake_platform, settings_store=await _hitl_settings_store(False)
    ) as client, client.websocket_connect("/ws/chat/hitl-off-thread") as ws:
        ws.send_json({"type": "user_message", "content": "write a file"})
        frames = _drain_turn(ws)

    assert frames[0] == {"type": "turn_start"}
    _assert_turn_end(frames[-1], "completed")
    assert not any(f["type"] == "approval_request" for f in frames)

    assert fake_platform.personal() == {"x.txt": b"y"}


async def test_thread_state_reflects_pending_approval_across_reconnect(
    fake_model: FakeModel, fake_platform: FakePlatform
) -> None:
    """Simulates a reconnect: query `/api/threads/{id}/state` with no WS turn
    running, after a previous turn left an approval pending."""
    fake_model.queue(ToolCallTurn(name="write_file", args={"file_path": "/personal/x.txt", "content": "y"}))
    thread_store = AutoCreateThreadStore()

    with _make_client(
        fake_model,
        fake_platform,
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


async def test_approval_response_resumes_after_reconnect(fake_model: FakeModel, fake_platform: FakePlatform) -> None:
    """A new WS connection must accept `approval_response` for an interrupt
    left pending by a previous connection (the checkpointer is the source
    of truth — see `chat_ws` hydrating `pending_approval` on connect)."""
    fake_model.queue(ToolCallTurn(name="write_file", args={"file_path": "/personal/x.txt", "content": "y"}))
    thread_store = AutoCreateThreadStore()

    with _make_client(
        fake_model,
        fake_platform,
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
    _assert_turn_end(resume_frames[-1], "completed")
    assert fake_platform.personal() == {"x.txt": b"y"}


class _GatedTurnStatsStore(InMemoryTurnStatsStore):
    """Holds the `awaiting_approval` write — which sits between `approval_request`
    and `turn_end` — until the test opens `gate` (from the server's loop)."""

    def __init__(self) -> None:
        super().__init__()
        self.gate = asyncio.Event()

    async def upsert(self, stat: TurnStat) -> None:
        if stat.status == "awaiting_approval":
            await self.gate.wait()
        await super().upsert(stat)


def _receive_json_within(ws, seconds: float) -> dict:
    """`ws.receive_json()` that fails instead of hanging the suite."""

    async def receive() -> dict:
        with anyio.fail_after(seconds):
            return await ws._send_rx.receive()

    message = ws.portal.call(receive)
    return json.loads(message["text"])


def _drain_turn_within(ws, seconds: float = 10) -> list[dict]:
    frames = []
    while True:
        frame = _receive_json_within(ws, seconds)
        frames.append(frame)
        if frame["type"] in ("turn_end", "error"):
            return frames


@pytest.mark.parametrize(
    ("decision", "expected_files"), [("approve", {"x.txt": b"y"}), ("reject", {})]
)
async def test_approval_response_sent_before_turn_end_is_applied(
    fake_model: FakeModel, fake_platform: FakePlatform, decision: str, expected_files: dict
) -> None:
    """#181: the UI shows the card on `approval_request`, so a fast click can
    land while the server is still finishing the turn (before `turn_end`)."""
    fake_model.queue(ToolCallTurn(name="write_file", args={"file_path": "/personal/x.txt", "content": "y"}))
    stats = _GatedTurnStatsStore()

    with _make_client(
        fake_model, fake_platform, settings_store=await _hitl_settings_store(True)
    ) as client, client.websocket_connect("/ws/chat/hitl-race-thread") as ws:
        client.app.state.turn_stats_store = stats
        ws.send_json({"type": "user_message", "content": "write a file"})
        approval = _receive_json_within(ws, 10)
        while approval["type"] != "approval_request":
            approval = _receive_json_within(ws, 10)

        fake_model.queue(TextTurn("done"))
        ws.send_json(
            {
                "type": "approval_response",
                "interrupt_id": approval["interrupt_id"],
                "decisions": [
                    {"tool_call_id": approval["actions"][0]["tool_call_id"], "decision": decision}
                ],
            }
        )
        ws.portal.call(stats.gate.set)

        _assert_turn_end(_drain_turn_within(ws)[-1], "awaiting_approval")
        resume_frames = _drain_turn_within(ws)

    assert resume_frames[0] == {"type": "turn_start"}
    _assert_turn_end(resume_frames[-1], "completed")
    assert fake_platform.personal() == expected_files


async def test_cancel_before_turn_end_reannounces_pending_approval(
    fake_model: FakeModel, fake_platform: FakePlatform
) -> None:
    """#189: a `cancel` between `approval_request` and `turn_end` can't
    un-pause the graph; the connection must end up awaiting approval again,
    with nothing rejected on the user's behalf."""
    fake_model.queue(ToolCallTurn(name="write_file", args={"file_path": "/personal/x.txt", "content": "y"}))
    stats = _GatedTurnStatsStore()

    with _make_client(
        fake_model, fake_platform, settings_store=await _hitl_settings_store(True)
    ) as client, client.websocket_connect("/ws/chat/hitl-cancel-race-thread") as ws:
        client.app.state.turn_stats_store = stats
        ws.send_json({"type": "user_message", "content": "write a file"})
        approval = _receive_json_within(ws, 10)
        while approval["type"] != "approval_request":
            approval = _receive_json_within(ws, 10)

        ws.send_json({"type": "cancel"})
        _assert_turn_end(_receive_json_within(ws, 10), "cancelled")
        ws.portal.call(stats.gate.set)

        reannounced = _drain_turn_within(ws)
        assert [f["type"] for f in reannounced] == ["approval_request", "turn_end"]
        assert reannounced[0] == approval
        _assert_turn_end(reannounced[-1], "awaiting_approval")
        assert fake_platform.personal() == {}

        fake_model.queue(TextTurn("done"))
        ws.send_json(
            {
                "type": "approval_response",
                "interrupt_id": approval["interrupt_id"],
                "decisions": [{"tool_call_id": approval["actions"][0]["tool_call_id"], "decision": "approve"}],
            }
        )
        resume_frames = _drain_turn_within(ws)

    assert resume_frames[0] == {"type": "turn_start"}
    _assert_turn_end(resume_frames[-1], "completed")
    assert fake_platform.personal() == {"x.txt": b"y"}


async def test_cancel_after_approval_response_mid_turn_applies_the_answer(
    fake_model: FakeModel, fake_platform: FakePlatform
) -> None:
    """#189: when the card was already answered before the cancel, the held
    answer is applied rather than re-asking."""
    fake_model.queue(ToolCallTurn(name="write_file", args={"file_path": "/personal/x.txt", "content": "y"}))
    stats = _GatedTurnStatsStore()

    with _make_client(
        fake_model, fake_platform, settings_store=await _hitl_settings_store(True)
    ) as client, client.websocket_connect("/ws/chat/hitl-cancel-answered-thread") as ws:
        client.app.state.turn_stats_store = stats
        ws.send_json({"type": "user_message", "content": "write a file"})
        approval = _receive_json_within(ws, 10)
        while approval["type"] != "approval_request":
            approval = _receive_json_within(ws, 10)

        fake_model.queue(TextTurn("done"))
        ws.send_json(
            {
                "type": "approval_response",
                "interrupt_id": approval["interrupt_id"],
                "decisions": [{"tool_call_id": approval["actions"][0]["tool_call_id"], "decision": "approve"}],
            }
        )
        ws.send_json({"type": "cancel"})
        _assert_turn_end(_receive_json_within(ws, 10), "cancelled")
        ws.portal.call(stats.gate.set)

        resume_frames = _drain_turn_within(ws)

    assert resume_frames[0] == {"type": "turn_start"}
    _assert_turn_end(resume_frames[-1], "completed")
    assert fake_platform.personal() == {"x.txt": b"y"}


async def test_stale_approval_response_mid_turn_is_still_ignored(
    fake_model: FakeModel, fake_platform: FakePlatform
) -> None:
    """A repeat click for an interrupt that's already been answered arrives
    mid-resume; it must not be replayed against the next interrupt (1008)."""
    fake_model.queue(
        ToolCallTurn(name="write_file", args={"file_path": "/personal/a.txt", "content": "1"}),
        ToolCallTurn(name="write_file", args={"file_path": "/personal/b.txt", "content": "2"}),
    )

    with _make_client(
        fake_model, fake_platform, settings_store=await _hitl_settings_store(True)
    ) as client, client.websocket_connect("/ws/chat/hitl-stale-thread") as ws:
        ws.send_json({"type": "user_message", "content": "write two files"})
        approval = next(f for f in _drain_turn_within(ws) if f["type"] == "approval_request")
        response = {
            "type": "approval_response",
            "interrupt_id": approval["interrupt_id"],
            "decisions": [{"tool_call_id": approval["actions"][0]["tool_call_id"], "decision": "approve"}],
        }
        stats = _GatedTurnStatsStore()
        client.app.state.turn_stats_store = stats
        ws.send_json(response)
        assert _receive_json_within(ws, 10) == {"type": "turn_start"}
        ws.send_json(response)
        ws.portal.call(stats.gate.set)

        second_turn = _drain_turn_within(ws)
        _assert_turn_end(second_turn[-1], "awaiting_approval")
        second = next(f for f in second_turn if f["type"] == "approval_request")
        assert second["interrupt_id"] != approval["interrupt_id"]

        fake_model.queue(TextTurn("done"))
        ws.send_json(
            {
                "type": "approval_response",
                "interrupt_id": second["interrupt_id"],
                "decisions": [{"tool_call_id": second["actions"][0]["tool_call_id"], "decision": "approve"}],
            }
        )
        resume_frames = _drain_turn_within(ws)

    _assert_turn_end(resume_frames[-1], "completed")
    assert fake_platform.personal() == {"a.txt": b"1", "b.txt": b"2"}


async def test_thread_state_is_null_when_nothing_pending(fake_model: FakeModel, fake_platform: FakePlatform) -> None:
    fake_model.queue(TextTurn("hello"))

    with _make_client(
        fake_model, fake_platform, settings_store=await _hitl_settings_store(True)
    ) as client:
        with client.websocket_connect("/ws/chat/hitl-state-empty-thread") as ws:
            ws.send_json({"type": "user_message", "content": "hi"})
            _drain_turn(ws)

        state_response = client.get("/api/threads/hitl-state-empty-thread/state")
        assert state_response.status_code == 200
        assert state_response.json() == {"pending_approval": None, "running": False}

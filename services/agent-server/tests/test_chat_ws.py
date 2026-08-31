"""Tests for the `/ws/chat/{thread_id}` WebSocket endpoint (`app/api/chat_ws.py`).

Uses `starlette.testclient.TestClient` (not `httpx.AsyncClient`/`ASGITransport`,
which don't support WebSockets the same way) — the standard, if synchronous,
tool for testing FastAPI WebSocket routes. It's used synchronously here even
though the rest of this suite is async (`asyncio_mode = "auto"`): entering
`with TestClient(app) as client:` runs the app's lifespan (building the real
agent against `app.state.settings`) on a dedicated background event loop
owned by the client, and `websocket_connect(...)` sessions talk to it via a
blocking, thread-safe portal — safe to drive from worker threads for the
concurrency test below.
"""

import threading

import pytest
from langgraph.checkpoint.memory import MemorySaver
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.db.threads import InMemoryThreadStore, ThreadStore
from app.main import create_app
from tests.fake_model.scripting import FakeModel, TextTurn, ToolCallTurn


def _make_client(fake_model: FakeModel, tmp_path, thread_store: ThreadStore | None = None) -> TestClient:
    settings = fake_model.settings(workspace_root=str(tmp_path))
    # `checkpointer_override`/`thread_store_override` keep this on
    # `MemorySaver`/`InMemoryThreadStore` (fast, no real Postgres) rather
    # than the production lifespan's real Postgres connection — see
    # `app.main.create_app`'s docstring. Callers that want to assert on
    # `ThreadStore` state (M3-02) pass their own `thread_store` instance in;
    # everyone else gets a private, unobservable one (unchanged behavior).
    app = create_app(
        settings,
        checkpointer_override=MemorySaver(),
        thread_store_override=thread_store or InMemoryThreadStore(),
    )
    return TestClient(app)


def _drain_turn(ws) -> list[dict]:
    """Receive frames until (and including) `turn_end` or `error`."""
    frames = []
    while True:
        frame = ws.receive_json()
        frames.append(frame)
        if frame["type"] in ("turn_end", "error"):
            return frames


async def test_plain_turn(fake_model: FakeModel, tmp_path) -> None:
    fake_model.queue(TextTurn("hello world", chunk_size=5))

    with _make_client(fake_model, tmp_path) as client, client.websocket_connect(
        "/ws/chat/plain-thread"
    ) as ws:
        ws.send_json({"type": "user_message", "content": "hi"})
        frames = _drain_turn(ws)

    assert frames[0] == {"type": "turn_start"}
    assert frames[-1] == {"type": "turn_end"}

    token_frames = frames[1:-1]
    assert len(token_frames) >= 2
    assert all(f["type"] == "token" for f in token_frames)
    assert "".join(f["content"] for f in token_frames) == "hello world"


async def test_tool_turn(fake_model: FakeModel, tmp_path) -> None:
    fake_model.queue(
        ToolCallTurn(name="write_file", args={"file_path": "/x.txt", "content": "y"}),
        TextTurn("done"),
    )

    with _make_client(fake_model, tmp_path) as client, client.websocket_connect(
        "/ws/chat/tool-thread"
    ) as ws:
        ws.send_json({"type": "user_message", "content": "write a file"})
        frames = _drain_turn(ws)

    assert frames[0] == {"type": "turn_start"}
    assert frames[-1] == {"type": "turn_end"}

    types = [f["type"] for f in frames]
    tool_start_idx = types.index("tool_start")
    tool_end_idx = types.index("tool_end")
    assert tool_start_idx < tool_end_idx

    tool_start = frames[tool_start_idx]
    assert tool_start["name"] == "write_file"
    assert tool_start["category"] == "file"
    assert tool_start["args"] == {"file_path": "/x.txt", "content": "y"}
    assert isinstance(tool_start["tool_call_id"], str) and tool_start["tool_call_id"]

    tool_end = frames[tool_end_idx]
    assert tool_end["name"] == "write_file"
    assert tool_end["status"] == "success"
    assert tool_end["tool_call_id"] == tool_start["tool_call_id"]

    # Token frames for "done" arrive after the tool_end, before turn_end.
    token_frames = [f for f in frames[tool_end_idx + 1 : -1] if f["type"] == "token"]
    assert token_frames
    assert "".join(f["content"] for f in token_frames) == "done"

    written = tmp_path / "x.txt"
    assert written.exists()
    assert written.read_text() == "y"


async def test_invalid_frame(fake_model: FakeModel, tmp_path) -> None:
    with _make_client(fake_model, tmp_path) as client, client.websocket_connect(
        "/ws/chat/invalid-thread"
    ) as ws:
        ws.send_json({"type": "not_a_real_type"})
        frame = ws.receive_json()
        assert frame["type"] == "error"
        assert isinstance(frame["message"], str) and frame["message"]

        with pytest.raises(WebSocketDisconnect) as exc_info:
            ws.receive_json()
        assert exc_info.value.code == 1008


async def test_invalid_frame_non_json_text(fake_model: FakeModel, tmp_path) -> None:
    with _make_client(fake_model, tmp_path) as client, client.websocket_connect(
        "/ws/chat/invalid-thread-2"
    ) as ws:
        ws.send_text("this is not json")
        frame = ws.receive_json()
        assert frame["type"] == "error"

        with pytest.raises(WebSocketDisconnect) as exc_info:
            ws.receive_json()
        assert exc_info.value.code == 1008


async def test_two_turns_same_socket(fake_model: FakeModel, tmp_path) -> None:
    fake_model.queue(TextTurn("first reply"), TextTurn("second reply"))

    with _make_client(fake_model, tmp_path) as client, client.websocket_connect(
        "/ws/chat/two-turns-thread"
    ) as ws:
        ws.send_json({"type": "user_message", "content": "message one"})
        first_frames = _drain_turn(ws)

        ws.send_json({"type": "user_message", "content": "message two"})
        second_frames = _drain_turn(ws)

    assert first_frames[0] == {"type": "turn_start"}
    assert first_frames[-1] == {"type": "turn_end"}
    assert "".join(f["content"] for f in first_frames if f["type"] == "token") == "first reply"

    assert second_frames[0] == {"type": "turn_start"}
    assert second_frames[-1] == {"type": "turn_end"}
    assert "".join(f["content"] for f in second_frames if f["type"] == "token") == "second reply"

    assert len(fake_model.requests) == 2
    second_request_contents = [m.get("content") for m in fake_model.requests[-1]["messages"]]
    assert any("message one" in (c or "") for c in second_request_contents)
    assert any("first reply" in (c or "") for c in second_request_contents)


def test_concurrent_turns_serialized(fake_model: FakeModel, tmp_path) -> None:
    fake_model.queue(TextTurn("first reply"), TextTurn("second reply"))
    thread_id = "concurrent-thread"

    results: dict[str, list[dict]] = {}
    errors: dict[str, BaseException] = {}
    start_barrier = threading.Barrier(2)

    def drive(key: str, ws, message: str) -> None:
        try:
            start_barrier.wait(timeout=5)
            ws.send_json({"type": "user_message", "content": message})
            results[key] = _drain_turn(ws)
        except BaseException as exc:  # noqa: BLE001
            errors[key] = exc

    with _make_client(fake_model, tmp_path) as client, client.websocket_connect(
        f"/ws/chat/{thread_id}"
    ) as ws_a, client.websocket_connect(f"/ws/chat/{thread_id}") as ws_b:
        t_a = threading.Thread(target=drive, args=("a", ws_a, "message-a"))
        t_b = threading.Thread(target=drive, args=("b", ws_b, "message-b"))
        t_a.start()
        t_b.start()
        t_a.join(timeout=10)
        t_b.join(timeout=10)

    assert not errors, errors
    assert set(results) == {"a", "b"}

    for frames in results.values():
        assert frames[0] == {"type": "turn_start"}
        assert frames[-1] == {"type": "turn_end"}

    # Both turns completed; the fake model saw exactly one request per turn.
    assert len(fake_model.requests) == 2

    # No interleaving: whichever request ran second must see the FULL first
    # exchange (both connections' user messages plus the first turn's
    # assistant reply) already in the checkpointed history — only possible
    # if the per-thread lock fully serialized the two turns rather than
    # letting them race against a shared/empty checkpoint.
    second_request_contents = [m.get("content") for m in fake_model.requests[-1]["messages"]]
    joined = [c or "" for c in second_request_contents]
    assert any("message-a" in c for c in joined)
    assert any("message-b" in c for c in joined)
    assert any("first reply" in c for c in joined)


async def test_title_autoset_and_updated_at_bump(fake_model: FakeModel, tmp_path) -> None:
    """M3-02: connection-time auto-insert, title auto-set from msg 1, `updated_at` bump."""
    fake_model.queue(TextTurn("first reply"), TextTurn("second reply"))
    thread_store = InMemoryThreadStore()
    thread_id = "title-bump-thread"
    long_message = "hello world " * 10  # > 60 chars once whitespace-collapsed

    with _make_client(fake_model, tmp_path, thread_store=thread_store) as client, client.websocket_connect(
        f"/ws/chat/{thread_id}"
    ) as ws:
        # `ensure_exists` runs once at connection-open, before any message.
        created = await thread_store.get(thread_id)
        assert created is not None
        assert created.title == "New chat"

        ws.send_json({"type": "user_message", "content": long_message})
        _drain_turn(ws)

        # A second turn's `turn_end` can only reach the client after the
        # server (a single sequential per-connection coroutine) has already
        # run turn 1's post-turn `thread_store.touch()` — that call always
        # executes before the server even attempts to receive message 2 —
        # so asserting here (rather than right after the FIRST `_drain_turn`)
        # avoids a race against an in-flight `touch()` call.
        ws.send_json({"type": "user_message", "content": "a totally different second message"})
        _drain_turn(ws)

        after_turns = await thread_store.get(thread_id)

    expected_title = " ".join(long_message.split())[:60] + "..."
    assert after_turns.title == expected_title
    assert after_turns.updated_at > created.updated_at

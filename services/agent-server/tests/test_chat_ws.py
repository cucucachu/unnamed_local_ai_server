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

from app.db.settings import InMemorySettingsStore, SettingsStore
from app.db.threads import InMemoryThreadStore, ThreadStore
from app.main import create_app
from tests.fake_exec_manager.scripting import FakeExecManager
from tests.fake_model.scripting import FakeModel, TextTurn, ToolCallTurn
from tests.fake_web_fetch.scripting import FakeWebFetch


def _make_client(
    fake_model: FakeModel,
    tmp_path,
    thread_store: ThreadStore | None = None,
    settings_store: SettingsStore | None = None,
) -> TestClient:
    settings = fake_model.settings(workspace_root=str(tmp_path))
    # `checkpointer_override`/`thread_store_override` keep this on
    # `MemorySaver`/`InMemoryThreadStore` (fast, no real Postgres) rather
    # than the production lifespan's real Postgres connection — see
    # `app.main.create_app`'s docstring. Callers that want to assert on
    # `ThreadStore` state (M3-02) pass their own `thread_store` instance in;
    # everyone else gets a private, unobservable one (unchanged behavior).
    # `settings_store` (M8-03): most pre-HITL tests here are about tool
    # execution mechanics, not the approval flow, so they pass one
    # pre-seeded with `hitl_enabled: False` (see `_no_hitl_settings_store`)
    # to keep exercising direct tool execution like before this ticket.
    app = create_app(
        settings,
        checkpointer_override=MemorySaver(),
        thread_store_override=thread_store or InMemoryThreadStore(),
        settings_store_override=settings_store or InMemorySettingsStore(),
    )
    return TestClient(app)


async def _no_hitl_settings_store() -> InMemorySettingsStore:
    store = InMemorySettingsStore()
    await store.update_document({"hitl_enabled": False})
    return store


async def _thinking_settings_store(*, thinking_enabled: bool) -> InMemorySettingsStore:
    store = InMemorySettingsStore()
    await store.update_document({"hitl_enabled": False, "thinking_enabled": thinking_enabled})
    return store


def _drain_turn(ws) -> list[dict]:
    """Receive frames until (and including) `turn_end` or `error`."""
    frames = []
    while True:
        frame = ws.receive_json()
        frames.append(frame)
        if frame["type"] in ("turn_end", "error"):
            return frames


def _assert_turn_end(frame: dict, status: str) -> None:
    """`turn_end` gained `duration_ms` in M9-02 — assert shape, not exact elapsed."""
    assert frame["type"] == "turn_end"
    assert frame["status"] == status
    assert isinstance(frame.get("duration_ms"), int)
    assert frame["duration_ms"] >= 0


async def test_plain_turn(fake_model: FakeModel, tmp_path) -> None:
    fake_model.queue(TextTurn("hello world", chunk_size=5))

    with _make_client(fake_model, tmp_path) as client, client.websocket_connect(
        "/ws/chat/plain-thread"
    ) as ws:
        ws.send_json({"type": "user_message", "content": "hi"})
        frames = _drain_turn(ws)

    assert frames[0] == {"type": "turn_start"}
    _assert_turn_end(frames[-1], "completed")

    token_frames = frames[1:-1]
    assert len(token_frames) >= 2
    assert all(f["type"] == "token" for f in token_frames)
    assert "".join(f["content"] for f in token_frames) == "hello world"


async def test_reasoning_frames_precede_tokens_when_thinking_on(
    fake_model: FakeModel, tmp_path
) -> None:
    """M8-07: scripted reasoning deltas become `reasoning` frames, then tokens."""
    fake_model.queue(
        TextTurn(
            "hello world",
            chunk_size=5,
            reasoning_content="The user said hi. I'll greet them.",
            reasoning_chunk_size=6,
        )
    )

    with _make_client(
        fake_model, tmp_path, settings_store=await _thinking_settings_store(thinking_enabled=True)
    ) as client, client.websocket_connect("/ws/chat/reasoning-on") as ws:
        ws.send_json({"type": "user_message", "content": "hi"})
        frames = _drain_turn(ws)

    assert frames[0] == {"type": "turn_start"}
    _assert_turn_end(frames[-1], "completed")

    mid = frames[1:-1]
    types = [f["type"] for f in mid]
    assert "reasoning" in types
    assert "token" in types
    assert types.index("reasoning") < types.index("token")
    assert all(t in ("reasoning", "token") for t in types)

    last_reasoning = max(i for i, t in enumerate(types) if t == "reasoning")
    first_token = types.index("token")
    assert last_reasoning < first_token

    assert "".join(f["content"] for f in mid if f["type"] == "reasoning") == (
        "The user said hi. I'll greet them."
    )
    assert "".join(f["content"] for f in mid if f["type"] == "token") == "hello world"

    assert fake_model.requests
    last_body = fake_model.requests[-1]
    kwargs = last_body.get("chat_template_kwargs") or {}
    assert kwargs.get("enable_thinking") is True


async def test_no_reasoning_frames_when_thinking_off(fake_model: FakeModel, tmp_path) -> None:
    """M8-07: thinking off binds enable_thinking=false; no reasoning frames."""
    fake_model.queue(
        TextTurn(
            "hello world",
            chunk_size=5,
            reasoning_content="This thought must not appear.",
            reasoning_chunk_size=6,
        )
    )

    with _make_client(
        fake_model, tmp_path, settings_store=await _thinking_settings_store(thinking_enabled=False)
    ) as client, client.websocket_connect("/ws/chat/reasoning-off") as ws:
        ws.send_json({"type": "user_message", "content": "hi"})
        frames = _drain_turn(ws)

    assert frames[0] == {"type": "turn_start"}
    _assert_turn_end(frames[-1], "completed")
    assert all(f["type"] != "reasoning" for f in frames)

    token_frames = [f for f in frames[1:-1] if f["type"] == "token"]
    assert "".join(f["content"] for f in token_frames) == "hello world"

    assert fake_model.requests
    last_body = fake_model.requests[-1]
    kwargs = last_body.get("chat_template_kwargs") or {}
    assert kwargs.get("enable_thinking") is False


async def test_tool_turn(fake_model: FakeModel, tmp_path) -> None:
    """`hitl_enabled: False` (M8-03): this test is about the `write_file`
    tool-call wire format, not the approval flow — see `test_hitl_*` below
    for HITL coverage of the exact same tool."""
    fake_model.queue(
        ToolCallTurn(name="write_file", args={"file_path": "/x.txt", "content": "y"}),
        TextTurn("done"),
    )

    with _make_client(
        fake_model, tmp_path, settings_store=await _no_hitl_settings_store()
    ) as client, client.websocket_connect("/ws/chat/tool-thread") as ws:
        ws.send_json({"type": "user_message", "content": "write a file"})
        frames = _drain_turn(ws)

    assert frames[0] == {"type": "turn_start"}
    _assert_turn_end(frames[-1], "completed")

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


async def test_execute_code_tool_turn(
    fake_model: FakeModel, fake_exec_manager: FakeExecManager, tmp_path
) -> None:
    """M4-04: `execute_code` reaches the fake exec-manager and its `tool_start`
    frame carries `category == "exec"` (extends M2-04's `_TOOL_CATEGORY_BY_NAME`
    mapping test, `test_tool_turn` above, to the new tool)."""
    fake_model.queue(
        ToolCallTurn(name="execute_code", args={"command": "echo hi"}),
        TextTurn("done"),
    )
    settings = fake_model.settings(
        workspace_root=str(tmp_path), exec_manager_url=fake_exec_manager.base_url
    )
    app = create_app(
        settings,
        checkpointer_override=MemorySaver(),
        thread_store_override=InMemoryThreadStore(),
        # `hitl_enabled: False` (M8-03): this test is about `execute_code`'s
        # wire format, not the approval flow.
        settings_store_override=await _no_hitl_settings_store(),
    )

    with TestClient(app) as client, client.websocket_connect("/ws/chat/exec-thread") as ws:
        ws.send_json({"type": "user_message", "content": "run echo hi"})
        frames = _drain_turn(ws)

    assert frames[0] == {"type": "turn_start"}
    _assert_turn_end(frames[-1], "completed")

    types = [f["type"] for f in frames]
    tool_start_idx = types.index("tool_start")
    tool_end_idx = types.index("tool_end")
    assert tool_start_idx < tool_end_idx

    tool_start = frames[tool_start_idx]
    assert tool_start["name"] == "execute_code"
    assert tool_start["category"] == "exec"

    tool_end = frames[tool_end_idx]
    assert tool_end["name"] == "execute_code"
    assert tool_end["status"] == "success"
    assert tool_end["tool_call_id"] == tool_start["tool_call_id"]

    assert fake_exec_manager.ensure_calls == ["exec-thread"]
    assert len(fake_exec_manager.execute_calls) == 1
    assert fake_exec_manager.execute_calls[0].command == "echo hi"
    assert fake_exec_manager.execute_calls[0].session_id == "exec-thread"


async def test_web_search_tool_turn(
    fake_model: FakeModel, fake_web_fetch: FakeWebFetch, tmp_path
) -> None:
    """M7-05: `web_search` reaches the fake web-fetch and its `tool_start`
    frame carries `category == "web"` (extends M2-04's
    `_TOOL_CATEGORY_BY_NAME` mapping test, `test_tool_turn` above, to the
    new tool)."""
    fake_web_fetch.search_response = {
        "query": "llama.cpp github repo",
        "results": [
            {
                "title": "ggml-org/llama.cpp",
                "url": "https://github.com/ggml-org/llama.cpp",
                "snippet": "LLM inference in C/C++",
                "engine": "github",
            }
        ],
    }
    fake_model.queue(
        ToolCallTurn(name="web_search", args={"query": "llama.cpp github repo"}),
        TextTurn("found it"),
    )
    settings = fake_model.settings(
        workspace_root=str(tmp_path), web_fetch_url=fake_web_fetch.base_url
    )
    app = create_app(
        settings, checkpointer_override=MemorySaver(), thread_store_override=InMemoryThreadStore()
    )

    with TestClient(app) as client, client.websocket_connect("/ws/chat/web-thread") as ws:
        ws.send_json({"type": "user_message", "content": "search for llama.cpp"})
        frames = _drain_turn(ws)

    assert frames[0] == {"type": "turn_start"}
    _assert_turn_end(frames[-1], "completed")

    types = [f["type"] for f in frames]
    tool_start_idx = types.index("tool_start")
    tool_end_idx = types.index("tool_end")
    assert tool_start_idx < tool_end_idx

    tool_start = frames[tool_start_idx]
    assert tool_start["name"] == "web_search"
    assert tool_start["category"] == "web"

    tool_end = frames[tool_end_idx]
    assert tool_end["name"] == "web_search"
    assert tool_end["status"] == "success"
    assert "https://github.com/ggml-org/llama.cpp" in tool_end["result_preview"]

    assert len(fake_web_fetch.search_calls) == 1
    assert fake_web_fetch.search_calls[0].q == "llama.cpp github repo"


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
    _assert_turn_end(first_frames[-1], "completed")
    assert "".join(f["content"] for f in first_frames if f["type"] == "token") == "first reply"

    assert second_frames[0] == {"type": "turn_start"}
    _assert_turn_end(second_frames[-1], "completed")
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
        _assert_turn_end(frames[-1], "completed")

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


async def test_cancel_mid_turn(fake_model: FakeModel, tmp_path) -> None:
    """M8-01: `cancel` sent mid-turn ends the turn early with
    `turn_end {"status": "cancelled"}`, releases the per-thread lock, and
    leaves the socket usable for a subsequent normal turn."""
    fake_model.queue(TextTurn("one two three four five six seven eight nine ten", chunk_size=4, chunk_delay_s=0.15))

    with _make_client(fake_model, tmp_path) as client, client.websocket_connect(
        "/ws/chat/cancel-thread"
    ) as ws:
        ws.send_json({"type": "user_message", "content": "count slowly from one to ten"})

        assert ws.receive_json() == {"type": "turn_start"}
        first_token = ws.receive_json()
        assert first_token["type"] == "token"

        ws.send_json({"type": "cancel"})

        frame = ws.receive_json()
        _assert_turn_end(frame, "cancelled")

        # The per-thread lock was released (cancel doesn't hold it past this
        # turn) and the connection is still open: a normal follow-up turn on
        # the SAME socket completes as usual.
        fake_model.queue(TextTurn("hello again"))
        ws.send_json({"type": "user_message", "content": "say hi"})
        frames = _drain_turn(ws)

    assert frames[0] == {"type": "turn_start"}
    _assert_turn_end(frames[-1], "completed")
    assert "".join(f["content"] for f in frames if f["type"] == "token") == "hello again"


async def test_cancel_outside_turn_is_noop(fake_model: FakeModel, tmp_path) -> None:
    """M8-01: `cancel` received while idle (no turn in flight) is silently
    ignored — no close, no error/turn_end frame, socket stays fully usable
    for a normal turn right after."""
    fake_model.queue(TextTurn("hello"))

    with _make_client(fake_model, tmp_path) as client, client.websocket_connect(
        "/ws/chat/cancel-noop-thread"
    ) as ws:
        ws.send_json({"type": "cancel"})

        # The very next frame received is `turn_start` for the following
        # `user_message` — proof the `cancel` produced no frame of its own
        # and didn't close the socket.
        ws.send_json({"type": "user_message", "content": "hi"})
        frames = _drain_turn(ws)

    assert frames[0] == {"type": "turn_start"}
    _assert_turn_end(frames[-1], "completed")
    assert "".join(f["content"] for f in frames if f["type"] == "token") == "hello"


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


async def test_truncate_then_run(fake_model: FakeModel, tmp_path) -> None:
    """M8-04: replace_from_message_id + mode=truncate drops from that user
    message onward, then runs the new HumanMessage. Title is not re-derived."""
    fake_model.queue(TextTurn("reply one"), TextTurn("reply two"), TextTurn("reply three"))
    thread_store = InMemoryThreadStore()
    thread_id = "truncate-then-run"

    with _make_client(fake_model, tmp_path, thread_store=thread_store) as client, client.websocket_connect(
        f"/ws/chat/{thread_id}"
    ) as ws:
        ws.send_json({"type": "user_message", "content": "turn one"})
        _drain_turn(ws)
        ws.send_json({"type": "user_message", "content": "turn two"})
        _drain_turn(ws)
        ws.send_json({"type": "user_message", "content": "turn three"})
        _drain_turn(ws)

        before = client.get(f"/api/threads/{thread_id}/messages").json()
        assert [m["role"] for m in before] == ["user", "assistant", "user", "assistant", "user", "assistant"]
        assert [m["content"] for m in before if m["role"] == "user"] == [
            "turn one",
            "turn two",
            "turn three",
        ]
        turn_two_id = before[2]["id"]
        assert before[2]["role"] == "user"
        title_after_three = (await thread_store.get(thread_id)).title

        fake_model.queue(TextTurn("edited reply"))
        ws.send_json(
            {
                "type": "user_message",
                "content": "turn two edited",
                "replace_from_message_id": turn_two_id,
                "mode": "truncate",
            }
        )
        frames = _drain_turn(ws)
        assert frames[0] == {"type": "turn_start"}
        _assert_turn_end(frames[-1], "completed")
        assert "".join(f["content"] for f in frames if f["type"] == "token") == "edited reply"

        after = client.get(f"/api/threads/{thread_id}/messages").json()

    assert [m["role"] for m in after] == ["user", "assistant", "user", "assistant"]
    assert [m["content"] for m in after if m["role"] == "user"] == ["turn one", "turn two edited"]
    assert [m["content"] for m in after if m["role"] == "assistant"] == ["reply one", "edited reply"]
    # Title stays the first-turn derivation, not the edited content.
    assert (await thread_store.get(thread_id)).title == title_after_three
    assert all(isinstance(m["id"], str) and m["id"] for m in after)


async def test_truncate_unknown_id(fake_model: FakeModel, tmp_path) -> None:
    """M8-04: replace_from_message_id that isn't in the checkpoint -> error + 1008."""
    fake_model.queue(TextTurn("hello"))

    with _make_client(fake_model, tmp_path) as client, client.websocket_connect(
        "/ws/chat/truncate-unknown"
    ) as ws:
        ws.send_json({"type": "user_message", "content": "hi"})
        _drain_turn(ws)

        ws.send_json(
            {
                "type": "user_message",
                "content": "edit missing",
                "replace_from_message_id": "does-not-exist",
                "mode": "truncate",
            }
        )
        frame = ws.receive_json()
        assert frame["type"] == "error"
        assert "unknown message id" in frame["message"]

        with pytest.raises(WebSocketDisconnect) as exc_info:
            ws.receive_json()
        assert exc_info.value.code == 1008


async def test_truncate_non_user_id(fake_model: FakeModel, tmp_path) -> None:
    """M8-04: replace_from_message_id pointing at an assistant row -> error + 1008."""
    fake_model.queue(TextTurn("hello"))
    thread_id = "truncate-non-user"

    with _make_client(fake_model, tmp_path) as client, client.websocket_connect(
        f"/ws/chat/{thread_id}"
    ) as ws:
        ws.send_json({"type": "user_message", "content": "hi"})
        _drain_turn(ws)

        messages = client.get(f"/api/threads/{thread_id}/messages").json()
        assistant = next(m for m in messages if m["role"] == "assistant")

        ws.send_json(
            {
                "type": "user_message",
                "content": "nope",
                "replace_from_message_id": assistant["id"],
                "mode": "truncate",
            }
        )
        frame = ws.receive_json()
        assert frame["type"] == "error"
        assert "user message" in frame["message"]

        with pytest.raises(WebSocketDisconnect) as exc_info:
            ws.receive_json()
        assert exc_info.value.code == 1008


def _user_contents(messages: list[dict]) -> list[str]:
    return [m["content"] for m in messages if m["role"] == "user"]


async def test_fork_produces_two_tips_and_switch_changes_history(
    fake_model: FakeModel, tmp_path
) -> None:
    """M8-05: fork keeps the old continuation; branches lists both tips;
    switching history + a new turn on the old branch extends that branch."""
    fake_model.queue(TextTurn("reply one"), TextTurn("reply two"), TextTurn("reply three"))
    thread_store = InMemoryThreadStore()
    thread_id = "fork-two-tips"

    with _make_client(fake_model, tmp_path, thread_store=thread_store) as client, client.websocket_connect(
        f"/ws/chat/{thread_id}"
    ) as ws:
        ws.send_json({"type": "user_message", "content": "turn one"})
        _drain_turn(ws)
        ws.send_json({"type": "user_message", "content": "turn two"})
        _drain_turn(ws)
        ws.send_json({"type": "user_message", "content": "turn three"})
        _drain_turn(ws)

        before = client.get(f"/api/threads/{thread_id}/messages").json()
        assert _user_contents(before) == ["turn one", "turn two", "turn three"]
        turn_two_id = before[2]["id"]
        assert before[2]["role"] == "user"

        fake_model.queue(TextTurn("forked reply"))
        ws.send_json(
            {
                "type": "user_message",
                "content": "turn two forked",
                "replace_from_message_id": turn_two_id,
                "mode": "fork",
            }
        )
        frames = _drain_turn(ws)
        assert frames[0] == {"type": "turn_start"}
        _assert_turn_end(frames[-1], "completed")
        assert "".join(f["content"] for f in frames if f["type"] == "token") == "forked reply"

        forked = client.get(f"/api/threads/{thread_id}/messages").json()
        assert _user_contents(forked) == ["turn one", "turn two forked"]
        assert [m["content"] for m in forked if m["role"] == "assistant"] == [
            "reply one",
            "forked reply",
        ]

        branches = client.get(f"/api/threads/{thread_id}/branches").json()
        assert len(branches) == 1
        point = branches[0]
        assert point["anchor_message_id"] == forked[2]["id"]
        assert len(point["branches"]) == 2
        assert point["active_index"] in (0, 1)
        active_tip = point["branches"][point["active_index"]]["checkpoint_id"]
        original_tip = next(
            b["checkpoint_id"] for b in point["branches"] if b["checkpoint_id"] != active_tip
        )
        assert {b["preview"] for b in point["branches"]} == {"turn two", "turn two forked"}

        put = client.put(
            f"/api/threads/{thread_id}/active_branch", json={"checkpoint_id": original_tip}
        )
        assert put.status_code == 204

        original = client.get(f"/api/threads/{thread_id}/messages").json()
        assert _user_contents(original) == ["turn one", "turn two", "turn three"]
        assert [m["content"] for m in original if m["role"] == "assistant"] == [
            "reply one",
            "reply two",
            "reply three",
        ]

        switched = client.get(f"/api/threads/{thread_id}/branches").json()
        assert switched[0]["active_index"] != point["active_index"]
        assert switched[0]["anchor_message_id"] == turn_two_id

        fake_model.queue(TextTurn("reply four"))
        ws.send_json({"type": "user_message", "content": "turn four"})
        _drain_turn(ws)

        extended = client.get(f"/api/threads/{thread_id}/messages").json()
        assert _user_contents(extended) == ["turn one", "turn two", "turn three", "turn four"]
        assert [m["content"] for m in extended if m["role"] == "assistant"][-1] == "reply four"

        after_extend = client.get(f"/api/threads/{thread_id}/branches").json()
        assert len(after_extend) == 1
        assert len(after_extend[0]["branches"]) == 2
        new_active = after_extend[0]["branches"][after_extend[0]["active_index"]]["checkpoint_id"]
        assert new_active != original_tip
        sibling = next(
            b["checkpoint_id"]
            for b in after_extend[0]["branches"]
            if b["checkpoint_id"] != new_active
        )

        assert (
            client.put(
                f"/api/threads/{thread_id}/active_branch", json={"checkpoint_id": sibling}
            ).status_code
            == 204
        )
        back_on_fork = client.get(f"/api/threads/{thread_id}/messages").json()
        assert _user_contents(back_on_fork) == ["turn one", "turn two forked"]
        assert "turn four" not in _user_contents(back_on_fork)

        record = await thread_store.get(thread_id)
        assert record is not None
        assert record.active_checkpoint_id == sibling


async def test_fork_unknown_and_non_user_id(fake_model: FakeModel, tmp_path) -> None:
    """M8-05: fork uses the same 1008 errors as truncate for bad ids."""
    fake_model.queue(TextTurn("hello"))
    thread_id = "fork-bad-id"

    with _make_client(fake_model, tmp_path) as client, client.websocket_connect(
        f"/ws/chat/{thread_id}"
    ) as ws:
        ws.send_json({"type": "user_message", "content": "hi"})
        _drain_turn(ws)
        messages = client.get(f"/api/threads/{thread_id}/messages").json()
        assistant = next(m for m in messages if m["role"] == "assistant")

        ws.send_json(
            {
                "type": "user_message",
                "content": "nope",
                "replace_from_message_id": assistant["id"],
                "mode": "fork",
            }
        )
        frame = ws.receive_json()
        assert frame["type"] == "error"
        assert "user message" in frame["message"]
        with pytest.raises(WebSocketDisconnect) as exc_info:
            ws.receive_json()
        assert exc_info.value.code == 1008


async def test_put_active_branch_404_if_not_a_tip(fake_model: FakeModel, tmp_path) -> None:
    """M8-05: PUT /active_branch 404s for an unknown checkpoint or thread."""
    fake_model.queue(TextTurn("hello"))
    thread_id = "fork-put-404"

    with _make_client(fake_model, tmp_path) as client, client.websocket_connect(
        f"/ws/chat/{thread_id}"
    ) as ws:
        ws.send_json({"type": "user_message", "content": "hi"})
        _drain_turn(ws)

        missing = client.put(
            f"/api/threads/{thread_id}/active_branch", json={"checkpoint_id": "not-a-checkpoint"}
        )
        assert missing.status_code == 404

        unknown_thread = client.put(
            "/api/threads/00000000-0000-0000-0000-000000000000/active_branch",
            json={"checkpoint_id": "anything"},
        )
        assert unknown_thread.status_code == 404

        empty = client.get(f"/api/threads/{thread_id}/branches").json()
        assert empty == []


async def test_user_message_id_is_stored_langchain_id(fake_model: FakeModel, tmp_path) -> None:
    """M8-04: an explicit `id` on user_message is the stored MessageOut.id."""
    fake_model.queue(TextTurn("hello"))
    thread_id = "stable-user-id"
    client_id = "11111111-1111-1111-1111-111111111111"

    with _make_client(fake_model, tmp_path) as client, client.websocket_connect(
        f"/ws/chat/{thread_id}"
    ) as ws:
        ws.send_json({"type": "user_message", "content": "hi", "id": client_id})
        _drain_turn(ws)
        messages = client.get(f"/api/threads/{thread_id}/messages").json()

    user = next(m for m in messages if m["role"] == "user")
    assert user["id"] == client_id


async def test_turn_end_duration_ms_and_history_turn_metadata(fake_model: FakeModel, tmp_path) -> None:
    """M9-02: `turn_end` carries `duration_ms`; GET .../messages attaches it
    to the final assistant row of the turn."""
    fake_model.queue(TextTurn("hello world"))
    thread_id = "duration-thread"

    with _make_client(fake_model, tmp_path) as client, client.websocket_connect(
        f"/ws/chat/{thread_id}"
    ) as ws:
        ws.send_json({"type": "user_message", "content": "hi"})
        frames = _drain_turn(ws)
        messages = client.get(f"/api/threads/{thread_id}/messages").json()

    _assert_turn_end(frames[-1], "completed")
    duration_ms = frames[-1]["duration_ms"]

    assistants = [m for m in messages if m["role"] == "assistant"]
    assert len(assistants) >= 1
    final = assistants[-1]
    assert final["content"] == "hello world"
    assert final["turn"] == {"status": "completed", "duration_ms": duration_ms}

    users = [m for m in messages if m["role"] == "user"]
    assert users[0].get("turn") is None


async def test_cancelled_turn_end_includes_duration_ms(fake_model: FakeModel, tmp_path) -> None:
    """M9-02: cancelled `turn_end` also carries `duration_ms`."""
    fake_model.queue(TextTurn("one two three four five six seven eight nine ten", chunk_size=4, chunk_delay_s=0.15))

    with _make_client(fake_model, tmp_path) as client, client.websocket_connect(
        "/ws/chat/cancel-duration-thread"
    ) as ws:
        ws.send_json({"type": "user_message", "content": "count slowly"})
        assert ws.receive_json() == {"type": "turn_start"}
        first_token = ws.receive_json()
        assert first_token["type"] == "token"
        ws.send_json({"type": "cancel"})
        frame = ws.receive_json()

    _assert_turn_end(frame, "cancelled")
    assert frame["duration_ms"] >= 0

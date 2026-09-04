"""`WS /ws/chat/{thread_id}` — token + tool streaming chat endpoint.

Wire format is fixed by docs/ARCHITECTURE.md's "Contracts" section (the
WebSocket chat protocol) — do not deviate; the frontend is built against it
exactly.

Client -> server (only valid incoming frame):
    {"type": "user_message", "content": "string"}

Server -> client (in order within a turn):
    {"type": "turn_start"}
    {"type": "token", "content": "str"}
    {"type": "tool_start", "tool_call_id": "str", "name": "str",
     "category": "file"|"exec"|"plan"|"other", "args": {}}
    {"type": "tool_end", "tool_call_id": "str", "name": "str",
     "status": "success"|"error", "result_preview": "str"}
    {"type": "turn_end"}
    {"type": "error", "message": "str"}   # then close, code 1011

Anything other than a well-formed `user_message` frame -> `error` frame, then
close code 1008 (policy violation).

## Event-shape notes (from real introspection against the M2-02 fake model,
`langchain-core==1.6.1` / `langgraph==1.2.11` / `deepagents==0.7.11` — see
M2-04's final report for the full transcript):

- `on_chat_model_stream`'s `data.chunk` is an `AIMessageChunk`. `.text` (a
  property, not the deprecated method) normalizes both plain-string and
  content-block-list `.content` shapes and already returns `""` for chunks
  that only carry `tool_call_chunks` (tool-call-argument deltas, no visible
  token) — so skipping empty `.text` handles both "empty chunk" and
  "tool-call-only chunk" in one check, no need to separately inspect
  `tool_call_chunks`.
- `on_tool_start`'s event dict does NOT carry the model's own tool-call id
  anywhere (checked `data` — only has `input` — and `metadata` in full).
  `on_tool_end` doesn't either (`data` has `input` and `output`). Only
  `on_tool_error` happens to carry `data.tool_call_id`. Since `tool_start`
  and `tool_end` frames must share one `tool_call_id` value for the frontend
  to correlate them, and the real id isn't available at `tool_start` time,
  this module uses the event's own `run_id` (a fresh UUID per tool
  invocation, confirmed stable across the `on_tool_start`/`on_tool_end`
  pair for the same call, and confirmed unique per call even within one
  multi-tool-call turn) as `tool_call_id` for BOTH frames instead. This is a
  deliberate deviation from a literal reading of the spec (documented in the
  ticket report) — it satisfies the actual purpose of the field
  (correlating start/end) without inventing a fake model tool-call id.
- Tool exception behavior (verified with a real erroring custom tool, not
  guessed): `langgraph.prebuilt.tool_node.ToolNode`'s default
  `handle_tool_errors` only catches its own internal `ToolInvocationError`
  (malformed tool-call args from the model) and turns THAT into a normal
  `on_tool_end` event whose output `ToolMessage.status == "error"` — handled
  below via the `status` check on `on_tool_end`. An arbitrary exception
  raised from inside a tool's own body is NOT swallowed by default: it fires
  an `on_tool_error` event (which this module turns into a `tool_end`
  `status: "error"` frame) and then PROPAGATES past the whole graph run,
  ending `astream_events` with that exception — i.e. contrary to this
  ticket's own text ("the agent loop itself continues; deepagents feeds
  errors back to the model"), that is only true for deepagents' *own*
  built-in filesystem tools, which catch their own errors internally and
  return a normal (status="success") `ToolMessage` whose `content` starts
  with `"Error: ..."` (see `deepagents/middleware/filesystem.py`) — it is
  NOT true for a generic tool exception. This module handles both real
  cases: `on_tool_error` -> `tool_end` (`status: "error"`), AND the
  subsequent propagated exception -> the normal "unhandled exception during
  a turn" path (`error` frame + close 1011).

## M3-02 `threads` table side-effects

This ticket adds `ThreadStore` bookkeeping side-effects around the existing
turn lifecycle (the wire format above is untouched — no new/changed frames):

- On connect (once per WS connection, before the receive loop): auto-insert
  a `threads` row for `thread_id` if one doesn't already exist, so a thread
  driven purely over WS (e.g. `scripts/ws_smoke.py`'s default `smoke-1`, or
  any pre-M3-02 gate script) still shows up for `GET /api/threads` /
  `GET .../messages` rather than 404ing there. See `PgThreadStore`'s
  docstring (`app/db/threads.py`) for what happens when `thread_id` isn't a
  valid UUID (the common case for these legacy/manual thread ids) — this
  call still can't fail the connection either way.
- On each well-formed `user_message`, before running the turn: set the
  thread's title to the first 60 chars of the message IF the title is still
  the default `"New chat"` (a no-op otherwise) — see `_derive_title`.
- After a turn completes normally (i.e. `_run_turn_or_disconnect` returns
  `False` — NOT on disconnect-mid-turn or an unhandled-error abort): bump
  `updated_at = now()`.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from typing import Any

from fastapi import APIRouter
from langchain_core.messages import HumanMessage
from starlette.websockets import WebSocket, WebSocketDisconnect

router = APIRouter()

# Per-thread_id lock, created on demand. Holding it for the full duration of
# a turn serializes concurrent connections on the same thread_id so their
# agent calls (and the shared MemorySaver checkpoint they read/write) can't
# interleave. Module-level and never evicted: thread_id cardinality is small
# and bounded by the number of chat threads a single-user server accumulates.
_thread_locks: dict[str, asyncio.Lock] = {}

# Exact category mapping from Conventions & Contracts §6.
_TOOL_CATEGORY_BY_NAME: dict[str, str] = {
    "ls": "file",
    "read_file": "file",
    "write_file": "file",
    "edit_file": "file",
    "glob": "file",
    "grep": "file",
    "delete": "file",
    "execute_code": "exec",
    "write_todos": "plan",
    "task": "plan",
}

_ARGS_VALUE_TRUNCATE_LEN = 500
_RESULT_PREVIEW_TRUNCATE_LEN = 2000
_TITLE_MAX_LEN = 60


def _derive_title(content: str) -> str:
    """First `_TITLE_MAX_LEN` chars of `content`, single-line, `...`-truncated.

    Spec (M3-02): "first 60 chars of the message (single-line, ellipsis if
    truncated)". Runs of whitespace (including newlines) are collapsed to a
    single space before truncating, so a multi-line message can't break a
    thread-list row onto multiple visual lines.
    """
    single_line = " ".join(content.split())
    if len(single_line) <= _TITLE_MAX_LEN:
        return single_line
    return single_line[:_TITLE_MAX_LEN] + "..."


def _category_for_tool(name: str) -> str:
    return _TOOL_CATEGORY_BY_NAME.get(name, "other")


def _truncate_arg_value(value: Any) -> Any:
    """Truncate a single tool-arg value for the `tool_start` frame.

    Judgement call (spec says "args values str-truncated to 500 chars
    each" without pinning down non-string values): only `str` values are
    truncated (to `_ARGS_VALUE_TRUNCATE_LEN` chars); other JSON-safe types
    (int, float, bool, None, list, dict) pass through unchanged so the
    frontend still sees their real type. Anything else (not JSON-safe) is
    stringified and then truncated, so the frame is always serializable.
    """
    if isinstance(value, str):
        return value[:_ARGS_VALUE_TRUNCATE_LEN]
    if value is None or isinstance(value, (bool, int, float, list, dict)):
        return value
    return str(value)[:_ARGS_VALUE_TRUNCATE_LEN]


def _truncated_args(args: Any) -> dict:
    if not isinstance(args, dict):
        return {"value": _truncate_arg_value(args)}
    return {k: _truncate_arg_value(v) for k, v in args.items()}


def _tool_result_preview(output: Any) -> str:
    """Extract the display string for a `tool_end` frame's `result_preview`.

    `data.output` on `on_tool_end` is normally a `ToolMessage` (occasionally
    a `Command`). Judgement call: preview the tool's actual result text
    (`.content`) rather than `str()` of the whole message object (which
    would include noisy `name=... tool_call_id=...` repr fields) when
    available; fall back to `str(output)` otherwise.
    """
    content = getattr(output, "content", None)
    source = content if content is not None else output
    return str(source)[:_RESULT_PREVIEW_TRUNCATE_LEN]


def _validate_user_message(raw: object) -> str | None:
    """Return the message `content` if `raw` is a well-formed `user_message` frame, else `None`."""
    if not isinstance(raw, dict):
        return None
    if raw.get("type") != "user_message":
        return None
    content = raw.get("content")
    if not isinstance(content, str):
        return None
    return content


def _frame_for_event(event: dict) -> dict | None:
    """Map one `astream_events` event to an outgoing frame dict, or `None` to skip it."""
    kind = event.get("event")
    data = event.get("data") or {}

    if kind == "on_chat_model_stream":
        chunk = data.get("chunk")
        text = getattr(chunk, "text", "")
        if not text:
            return None
        return {"type": "token", "content": text}

    if kind == "on_tool_start":
        name = event.get("name", "")
        return {
            "type": "tool_start",
            "tool_call_id": str(event.get("run_id", "")),
            "name": name,
            "category": _category_for_tool(name),
            "args": _truncated_args(data.get("input")),
        }

    if kind == "on_tool_end":
        name = event.get("name", "")
        output = data.get("output")
        status = getattr(output, "status", "success")
        if status not in ("success", "error"):
            status = "success"
        return {
            "type": "tool_end",
            "tool_call_id": str(event.get("run_id", "")),
            "name": name,
            "status": status,
            "result_preview": _tool_result_preview(output),
        }

    if kind == "on_tool_error":
        name = event.get("name", "")
        error = data.get("error")
        return {
            "type": "tool_end",
            "tool_call_id": str(event.get("run_id", "")),
            "name": name,
            "status": "error",
            "result_preview": repr(error)[:_RESULT_PREVIEW_TRUNCATE_LEN],
        }

    return None


async def _run_turn(websocket: WebSocket, thread_id: str, content: str) -> None:
    agent = websocket.app.state.agent

    await websocket.send_json({"type": "turn_start"})
    async for event in agent.astream_events(
        {"messages": [HumanMessage(content=content)]},
        config={"configurable": {"thread_id": thread_id}},
        version="v2",
    ):
        frame = _frame_for_event(event)
        if frame is not None:
            await websocket.send_json(frame)
    await websocket.send_json({"type": "turn_end"})


async def _watch_for_disconnect(websocket: WebSocket) -> None:
    """Block until the client disconnects, ignoring any other message.

    A well-behaved client never sends another frame before a turn's
    `turn_end`/`error`; if one arrives anyway we just keep watching rather
    than misinterpreting it as a disconnect (v1 doesn't define behavior for
    that case).
    """
    while True:
        message = await websocket.receive()
        if message["type"] == "websocket.disconnect":
            return


async def _run_turn_or_disconnect(websocket: WebSocket, thread_id: str, content: str) -> bool:
    """Run one turn, racing it against a disconnect watcher.

    Returns `True` if the client disconnected mid-turn (the turn task is
    cancelled and the caller should stop processing this connection).
    Returns `False` if the turn ran to completion. Propagates any exception
    the turn itself raised (unhandled model/agent error).
    """
    turn_task = asyncio.create_task(_run_turn(websocket, thread_id, content))
    watch_task = asyncio.create_task(_watch_for_disconnect(websocket))
    try:
        done, _pending = await asyncio.wait(
            {turn_task, watch_task}, return_when=asyncio.FIRST_COMPLETED
        )
        if turn_task in done:
            turn_task.result()  # re-raises if the turn itself raised
            return False

        # Disconnect observed mid-turn: cancel the in-flight turn and let the
        # per-thread lock release cleanly via the caller's `async with`.
        turn_task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await turn_task
        return True
    finally:
        if not watch_task.done():
            watch_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await watch_task


async def _send_error_and_close(websocket: WebSocket, message: str, code: int) -> None:
    with contextlib.suppress(Exception):
        await websocket.send_json({"type": "error", "message": message})
    with contextlib.suppress(Exception):
        await websocket.close(code=code)


@router.websocket("/ws/chat/{thread_id}")
async def chat_ws(websocket: WebSocket, thread_id: str) -> None:
    await websocket.accept()

    thread_store = websocket.app.state.thread_store
    await thread_store.ensure_exists(thread_id)

    while True:
        try:
            raw = await websocket.receive_json()
        except WebSocketDisconnect:
            return
        except (json.JSONDecodeError, UnicodeDecodeError, KeyError, TypeError):
            await _send_error_and_close(
                websocket, "invalid frame: expected JSON text", code=1008
            )
            return

        content = _validate_user_message(raw)
        if content is None:
            await _send_error_and_close(
                websocket,
                'invalid frame: expected {"type": "user_message", "content": <str>}',
                code=1008,
            )
            return

        await thread_store.set_title_if_new(thread_id, _derive_title(content))

        lock = _thread_locks.setdefault(thread_id, asyncio.Lock())
        async with lock:
            try:
                disconnected = await _run_turn_or_disconnect(websocket, thread_id, content)
            except WebSocketDisconnect:
                return
            except Exception as exc:  # noqa: BLE001 - spec: any unhandled turn error -> `error` frame + close 1011
                await _send_error_and_close(websocket, str(exc), code=1011)
                return
        if disconnected:
            return
        await thread_store.touch(thread_id)

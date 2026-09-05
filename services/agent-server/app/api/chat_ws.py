"""`WS /ws/chat/{thread_id}` — token + tool streaming chat endpoint.

Wire format is fixed by docs/ARCHITECTURE.md's "Contracts" section (the
WebSocket chat protocol) — do not deviate; the frontend is built against it
exactly.

Client -> server (four valid incoming frames):
    {"type": "user_message", "content": "string"}
    {"type": "cancel"}   # M8-01: cancels a running turn; M8-03: while
                          # awaiting approval, rejects all pending actions
    {"type": "approval_response", "interrupt_id": "str",
     "decisions": [{"tool_call_id": "str", "decision": "approve"|"reject"}]}
     # M8-03: only valid while awaiting approval (see below)

Server -> client (in order within a turn):
    {"type": "turn_start"}
    {"type": "token", "content": "str"}
    {"type": "tool_start", "tool_call_id": "str", "name": "str",
     "category": "file"|"exec"|"plan"|"web"|"other", "args": {}}
    {"type": "tool_end", "tool_call_id": "str", "name": "str",
     "status": "success"|"error", "result_preview": "str"}
    {"type": "approval_request", "interrupt_id": "str",
     "actions": [{"tool_call_id", "name", "category", "args", "description"}]}
     # M8-03: emitted instead of a normal completion when the turn ends with
     # one or more mutating tool calls paused for human approval; ALWAYS
     # immediately followed by `turn_end {"status": "awaiting_approval"}`
     # (no `turn_end {"status": "completed"}` for that turn).
    {"type": "turn_end", "status": "completed"|"cancelled"|"awaiting_approval"}
    {"type": "error", "message": "str"}   # then close, code 1011

Anything other than a well-formed `user_message` frame, received while idle
(i.e. NOT mid-turn, NOT awaiting approval) -> `error` frame, then close code
1008 (policy violation) — this excludes `{"type": "cancel"}`, which is a
no-op outside a turn (see `chat_ws` below) rather than a close. Any frame
received WHILE a turn is in flight is handled by `_watch_inbound` instead:
`{"type": "cancel"}` cancels the turn (see M8-01 notes below); every other
frame received mid-turn is ignored (looped past), matching v1's existing
"a well-behaved client never sends another frame before turn_end/error"
assumption for anything other than `cancel`. While AWAITING APPROVAL (i.e.
the previous `turn_end` had `status: "awaiting_approval"`), the only two
valid inbound frames are `approval_response` (matching the pending
`interrupt_id`, with one decision per pending `tool_call_id`) and `cancel`
(reject-all); anything else -> `error` frame + close 1008, same as an
invalid frame while idle.

## M8-03 human-in-the-loop approvals (`interrupt_on`)

- `build_agent` (`app/agent/build.py`) installs `HumanInTheLoopMiddleware`
  for the four mutating tools (`write_file`, `edit_file`, `delete`,
  `execute_code`) via `interrupt_on=...`, gated per-turn by a `when`
  predicate reading `config["configurable"]["hitl_enabled"]`. **Finding**
  (see `build.py`'s module docstring for the full empirical writeup): the
  direct `InterruptOnConfig.when` predicate mechanism works as-is with the
  installed `deepagents==0.7.11`/`langchain==1.6.x` — no dual-compiled-graph
  fallback was needed. This module is the ONLY thing that sets
  `hitl_enabled` in `configurable` — read fresh from `SettingsStore` at the
  start of every turn (`_current_hitl_enabled` below), so a mid-conversation
  settings change takes effect on the very next turn.
- After a turn's `astream_events` stream completes (fresh OR resumed —
  see below), `_run_turn` checks `agent.aget_state(config).tasks[*].
  interrupts` for a pending `Interrupt`. `HumanInTheLoopMiddleware.
  after_model` always raises exactly one combined `Interrupt` per paused
  `AIMessage` (`langgraph`'s own `interrupt()` call takes a single
  `HITLRequest` covering every tool call needing review in that message,
  confirmed by reading `langchain/agents/middleware/human_in_the_loop.py`),
  so at most one `Interrupt` is ever pending at a time — never a list to
  fan out over.
- The raw `Interrupt.value` (a `HITLRequest`: `{"action_requests": [...],
  "review_configs": [...]}`) does NOT carry a `tool_call_id` per action —
  only `name`/`args`/`description`. `_pending_approval_from_state` (shared
  with `GET /api/threads/{id}/state` in `app/api/chat.py`) recovers the
  ids by re-reading the checkpointed state's last `AIMessage.tool_calls`
  and zipping the subset whose `name` is one of the four mutating tools
  (in original call order) against `action_requests` (built in that exact
  same subset+order by `HumanInTheLoopMiddleware.after_model` — see
  `build.py`'s `_hitl_enabled`, which returns the same bool for every
  mutating tool call in a turn, so "which calls interrupted" is fully
  determined by tool name membership alone, not by call-specific state).
  This needs no extra persistent storage: everything is reconstructed from
  the checkpointer's own state on every read.
- Resuming: `{"type": "approval_response", ...}` (or a `cancel`,
  reject-all) is turned into an ordered `decisions` list (one entry per
  pending `tool_call_id`, in the SAME order `_pending_approval_from_state`
  emitted them) and run via `agent.astream_events(Command(resume=
  {"decisions": [...]}), config=..., version="v2")` — confirmed against
  `HumanInTheLoopMiddleware._process_decision` that this is the exact
  expected shape: `{"type": "approve"}` / `{"type": "reject", "message":
  "..."}`. This resumed execution runs through the exact same `_run_turn`/
  `_run_turn_or_interrupt` machinery as a fresh `user_message` turn — a
  full `turn_start` ... (streaming) ... `turn_end` cycle on the same
  per-thread lock — and can itself end in `"completed"`, `"cancelled"` (if
  the client sends ANOTHER `cancel` while this resumed turn is actively
  streaming — the normal M8-01 path, since the graph is running again, not
  paused), or `"awaiting_approval"` again (a later mutating tool call in
  the same resumed run).
- `cancel` received while awaiting approval is NOT the M8-01
  disconnect/cancel-a-running-task path — there is no running task; the
  graph is paused on the interrupt. It's handled by resuming with an
  all-`"reject"` decision list, message `"The user cancelled."`, exactly
  like a rejected `approval_response` (same `Command(resume=...)` +
  `_run_turn_or_interrupt` call), so the model still gets a normal
  rejection `ToolMessage` and the conversation can continue.
- `chat_ws`'s own receive loop tracks `pending_approval` (the dict
  `_pending_approval_from_state` returned for the CURRENT thread's last
  turn, or `None`) as local state for the lifetime of one WS connection —
  purely a routing aid for "is the next inbound frame validated against
  `user_message` rules or `approval_response` rules"; it is NOT the source
  of truth. A reconnect re-derives it fresh from the checkpointer both
  server-side (on WS accept, so `approval_response` on the new socket
  works) and client-side (`GET /api/threads/{id}/state`, see
  `app/api/chat.py`).

## M8-01 `cancel` frame notes

- `_watch_inbound` (replacing `_watch_for_disconnect`) races the turn task
  exactly like the old disconnect watcher, but can now resolve two ways:
  `"disconnect"` (client socket closed) or `"cancel"` (client sent
  `{"type": "cancel"}`). On `"cancel"`: the turn task is cancelled and
  awaited (suppressing `CancelledError`), a `turn_end
  {"status": "cancelled"}` frame is sent, and — unlike a disconnect — the
  connection is kept open, the per-thread lock is released normally (via
  the caller's `async with lock:` exiting), and `thread_store.touch()`
  still runs, so the next `user_message` on the same socket works exactly
  like any other turn.
- Cancelling the turn task cancels the in-flight `astream_events` iterator,
  which propagates the cancellation down through the model call. For the
  chat-completion model node this tears down the underlying `httpx`
  streaming request to `model-runner`; verified live (Tier A) against a
  real `llama-server` that it aborts generation when the client request is
  cancelled — see the ticket report for the exact log line observed.
- Partial assistant text from a cancelled turn is NOT persisted: LangGraph
  never checkpoints the interrupted model node, so the partial text isn't
  in the thread's history (a page reload / history hydration loses it — the
  UI only keeps it, greyed out with a "Stopped" caption, for the current
  session). See `docs/ARCHITECTURE.md` §3 for the user-facing documentation
  of this limitation.
- `execute_code` mid-flight: cancelling the turn only cancels the agent
  server's own HTTP call to `code-exec-manager`; it does NOT stop the
  sandboxed command itself, which keeps running server-side until its own
  timeout. Acceptable per the ticket's explicit "out of scope" — documented
  here and in `docs/ARCHITECTURE.md` §3.

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
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.types import Command, StateSnapshot
from starlette.websockets import WebSocket, WebSocketDisconnect

from app.agent.build import MUTATING_TOOL_NAMES

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
    "web_search": "web",
    "web_fetch": "web",
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


def _pending_approval_from_state(state: StateSnapshot) -> dict | None:
    """Derive the `approval_request`/`GET .../state` payload from checkpointed state.

    Returns `{"interrupt_id": str, "actions": [{"tool_call_id", "name",
    "category", "args", "description"}]}` or `None` if there's no pending
    interrupt. See the module docstring's M8-03 section for the full
    tool_call_id-recovery reasoning: `HumanInTheLoopMiddleware.after_model`
    raises exactly one `Interrupt` whose `value["action_requests"]` doesn't
    carry a `tool_call_id`, so this zips it against the last `AIMessage`'s
    tool calls filtered to `MUTATING_TOOL_NAMES` (same subset+order the
    middleware itself used to build `action_requests`).
    """
    interrupts = [i for task in state.tasks for i in task.interrupts]
    if not interrupts:
        return None
    interrupt = interrupts[0]
    value = interrupt.value or {}
    action_requests = value.get("action_requests") or []
    if not action_requests:
        return None

    messages = state.values.get("messages", [])
    last_ai_msg = next((m for m in reversed(messages) if isinstance(m, AIMessage)), None)
    tool_call_ids = (
        [tc["id"] for tc in last_ai_msg.tool_calls if tc["name"] in MUTATING_TOOL_NAMES]
        if last_ai_msg is not None
        else []
    )

    actions = []
    for idx, action_request in enumerate(action_requests):
        name = action_request.get("name", "")
        tool_call_id = tool_call_ids[idx] if idx < len(tool_call_ids) else ""
        actions.append(
            {
                "tool_call_id": tool_call_id,
                "name": name,
                "category": _category_for_tool(name),
                "args": _truncated_args(action_request.get("args")),
                "description": action_request.get("description", ""),
            }
        )
    return {"interrupt_id": str(interrupt.id), "actions": actions}


async def get_pending_approval(agent: Any, thread_id: str) -> dict | None:
    """Public helper for `GET /api/threads/{id}/state` (`app/api/chat.py`).

    Re-derives the pending approval purely from the checkpointer's own
    state — no extra persistent storage needed (see module docstring).
    """
    state = await agent.aget_state({"configurable": {"thread_id": thread_id}})
    return _pending_approval_from_state(state)


def _reject_all_decisions(pending_approval: dict, message: str) -> list[dict]:
    return [{"type": "reject", "message": message} for _ in pending_approval["actions"]]


def _decisions_from_approval_response(raw: object, pending_approval: dict) -> list[dict] | None:
    """Validate an `approval_response` frame against the pending approval.

    Returns the ordered `decisions` list (matching `pending_approval
    ["actions"]`'s order, ready for `Command(resume={"decisions": ...})`)
    or `None` if the frame is malformed, doesn't match the currently
    pending `interrupt_id`, is missing a decision for any pending
    `tool_call_id`, or contains an invalid `decision` value.
    """
    if not isinstance(raw, dict) or raw.get("type") != "approval_response":
        return None
    if raw.get("interrupt_id") != pending_approval["interrupt_id"]:
        return None
    decisions_in = raw.get("decisions")
    if not isinstance(decisions_in, list):
        return None
    by_tool_call_id: dict[str, Any] = {}
    for entry in decisions_in:
        if not isinstance(entry, dict):
            return None
        tool_call_id = entry.get("tool_call_id")
        if not isinstance(tool_call_id, str):
            return None
        by_tool_call_id[tool_call_id] = entry.get("decision")

    ordered: list[dict] = []
    for action in pending_approval["actions"]:
        decision = by_tool_call_id.get(action["tool_call_id"])
        if decision == "approve":
            ordered.append({"type": "approve"})
        elif decision == "reject":
            ordered.append({"type": "reject", "message": "The user rejected this action."})
        else:
            return None
    return ordered


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


async def _run_turn(
    websocket: WebSocket, thread_id: str, run_input: Any, hitl_enabled: bool
) -> tuple[str, dict | None]:
    """Run one turn (fresh `user_message` OR a resumed `Command(resume=...)`).

    `run_input` is either `{"messages": [HumanMessage(...)]}` (a fresh turn)
    or a `langgraph.types.Command(resume={"decisions": [...]})` (M8-03:
    resuming a paused approval, from either `approval_response` or a
    reject-all `cancel`-while-awaiting-approval).

    Returns `(status, pending_approval)`: `status` is `"completed"` or
    `"awaiting_approval"` (see module docstring's M8-03 section for when
    each fires); `pending_approval` is the dict `_pending_approval_from_state`
    returned (only non-`None` when `status == "awaiting_approval"`).
    """
    agent = websocket.app.state.agent
    config = {"configurable": {"thread_id": thread_id, "hitl_enabled": hitl_enabled}}

    await websocket.send_json({"type": "turn_start"})
    async for event in agent.astream_events(run_input, config=config, version="v2"):
        frame = _frame_for_event(event)
        if frame is not None:
            await websocket.send_json(frame)

    state = await agent.aget_state(config)
    pending_approval = _pending_approval_from_state(state)
    if pending_approval is not None:
        await websocket.send_json(
            {
                "type": "approval_request",
                "interrupt_id": pending_approval["interrupt_id"],
                "actions": pending_approval["actions"],
            }
        )
        await websocket.send_json({"type": "turn_end", "status": "awaiting_approval"})
        return "awaiting_approval", pending_approval

    await websocket.send_json({"type": "turn_end", "status": "completed"})
    return "completed", None


def _is_cancel_frame(raw: object) -> bool:
    return isinstance(raw, dict) and raw.get("type") == "cancel"


async def _watch_inbound(websocket: WebSocket) -> str:
    """Block until either the client disconnects or sends a `cancel` frame.

    Returns `"disconnect"` or `"cancel"`. Any other frame received mid-turn
    (well-formed or not) is ignored — looped past — rather than
    misinterpreted as either of those two outcomes; v1 defines no other
    inbound behavior mid-turn.
    """
    while True:
        message = await websocket.receive()
        if message["type"] == "websocket.disconnect":
            return "disconnect"

        text = message.get("text")
        if text is None:
            continue
        try:
            raw = json.loads(text)
        except (json.JSONDecodeError, UnicodeDecodeError):
            continue
        if _is_cancel_frame(raw):
            return "cancel"


async def _run_turn_or_interrupt(
    websocket: WebSocket, thread_id: str, run_input: Any, hitl_enabled: bool
) -> tuple[str, dict | None]:
    """Run one turn (see `_run_turn`), racing it against `_watch_inbound`.

    Returns `(status, pending_approval)`. `status` is `"completed"` or
    `"awaiting_approval"` if the turn ran to completion (see `_run_turn`),
    `"cancelled"` if a `cancel` frame arrived while the turn was ACTIVELY
    STREAMING (a `turn_end {"status": "cancelled"}` frame has already been
    sent and the connection is still open — `pending_approval` is always
    `None` in this case, since a cancelled-mid-stream turn can't also have
    produced a pending interrupt), or `"disconnected"` if the client's
    socket closed mid-turn (the caller should stop processing this
    connection; no frame is sent — the socket is already gone). Propagates
    any exception the turn itself raised (unhandled model/agent error).
    """
    turn_task = asyncio.create_task(_run_turn(websocket, thread_id, run_input, hitl_enabled))
    watch_task = asyncio.create_task(_watch_inbound(websocket))
    try:
        done, _pending = await asyncio.wait(
            {turn_task, watch_task}, return_when=asyncio.FIRST_COMPLETED
        )
        if turn_task in done:
            return turn_task.result()  # re-raises if the turn itself raised

        outcome = watch_task.result()  # "disconnect" or "cancel"

        # Either way, cancel the in-flight turn; the per-thread lock (held
        # by the caller's `async with`) releases cleanly once we return.
        turn_task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await turn_task

        if outcome == "cancel":
            await websocket.send_json({"type": "turn_end", "status": "cancelled"})
            return "cancelled", None
        return "disconnected", None
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


async def _current_hitl_enabled(websocket: WebSocket) -> bool:
    """Fresh per-turn read of `SettingsStore.get_document().hitl_enabled` (M8-03).

    Read at the START of every turn (fresh AND resumed) rather than cached
    for the connection's lifetime, so a mid-conversation settings change
    (`PUT /api/settings`) takes effect on the very next turn.
    """
    settings_store = websocket.app.state.settings_store
    document = await settings_store.get_document()
    return document.hitl_enabled


@router.websocket("/ws/chat/{thread_id}")
async def chat_ws(websocket: WebSocket, thread_id: str) -> None:
    await websocket.accept()

    thread_store = websocket.app.state.thread_store
    await thread_store.ensure_exists(thread_id)

    # M8-03: local routing state for this connection only — `None` while
    # idle/mid-turn, set to the dict `_run_turn` returned right after a
    # `turn_end {"status": "awaiting_approval"}`. See module docstring's
    # M8-03 section: this is NOT the source of truth (a reconnect re-derives
    # it from the checkpointer via `GET /api/threads/{id}/state`). A fresh
    # socket must still *accept* `approval_response`/`cancel` for an
    # already-pending interrupt, so we hydrate this routing aid from the
    # checkpointer on connect rather than starting at `None` and treating
    # a legitimate resume as an invalid idle-frame (1008).
    pending_approval: dict | None = await get_pending_approval(
        websocket.app.state.agent, thread_id
    )

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

        lock = _thread_locks.setdefault(thread_id, asyncio.Lock())

        if pending_approval is not None:
            # M8-03: while awaiting approval, only `approval_response`
            # (matching the pending interrupt) or `cancel` (reject-all) are
            # valid — anything else is an invalid frame -> error + close
            # 1008, same treatment as an invalid frame while idle.
            if _is_cancel_frame(raw):
                decisions = _reject_all_decisions(pending_approval, "The user cancelled.")
            else:
                decisions = _decisions_from_approval_response(raw, pending_approval)
                if decisions is None:
                    await _send_error_and_close(
                        websocket,
                        "invalid frame: expected a matching approval_response "
                        "or cancel while awaiting approval",
                        code=1008,
                    )
                    return

            hitl_enabled = await _current_hitl_enabled(websocket)
            run_input = Command(resume={"decisions": decisions})
            async with lock:
                try:
                    outcome, new_pending = await _run_turn_or_interrupt(
                        websocket, thread_id, run_input, hitl_enabled
                    )
                except WebSocketDisconnect:
                    return
                except Exception as exc:  # noqa: BLE001 - spec: any unhandled turn error -> `error` frame + close 1011
                    await _send_error_and_close(websocket, str(exc), code=1011)
                    return
            if outcome == "disconnected":
                return
            pending_approval = new_pending
            await thread_store.touch(thread_id)
            continue

        # M8-01: `cancel` received while idle (no turn in flight, no pending
        # approval) is a no-op — ignored, not a validation error, not a
        # close. Checked before `_validate_user_message` so it never falls
        # through to the invalid-frame/1008 path below.
        if _is_cancel_frame(raw):
            continue

        content = _validate_user_message(raw)
        if content is None:
            await _send_error_and_close(
                websocket,
                'invalid frame: expected {"type": "user_message", "content": <str>}',
                code=1008,
            )
            return

        await thread_store.set_title_if_new(thread_id, _derive_title(content))
        hitl_enabled = await _current_hitl_enabled(websocket)

        async with lock:
            try:
                outcome, new_pending = await _run_turn_or_interrupt(
                    websocket, thread_id, {"messages": [HumanMessage(content=content)]}, hitl_enabled
                )
            except WebSocketDisconnect:
                return
            except Exception as exc:  # noqa: BLE001 - spec: any unhandled turn error -> `error` frame + close 1011
                await _send_error_and_close(websocket, str(exc), code=1011)
                return
        if outcome == "disconnected":
            return
        pending_approval = new_pending
        await thread_store.touch(thread_id)

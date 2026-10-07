"""`WS /ws/chat/{thread_id}` — token + tool streaming chat endpoint.

Wire format is fixed by docs/ARCHITECTURE.md's "Contracts" section (the
WebSocket chat protocol) — do not deviate; the frontend is built against it
exactly.

Client -> server (four valid incoming frames):
    {"type": "user_message", "content": "string",
     "replace_from_message_id": "str?",   # M8-04: optional; truncate/fork from here
     "mode": "truncate"|"fork"?}          # M8-04: optional; default SettingsStore.edit_mode_default
    {"type": "cancel"}   # M8-01: cancels a running turn; M8-03: while
                          # awaiting approval, rejects all pending actions
    {"type": "approval_response", "interrupt_id": "str",
     "decisions": [{"tool_call_id": "str", "decision": "approve"|"reject"}]}
     # M8-03: only valid while awaiting approval (see below)

Server -> client (in order within a turn):
    {"type": "turn_start"}
     # M17-01: a socket that connects while a turn runs instead gets
     # {"type": "turn_start", "replay": true, "user_message"?: {"id", "content"}}
     # then every frame that turn has sent so far, then the live frames.
    {"type": "reasoning", "content": "str"}  # M8-07: thought deltas; not persisted
    {"type": "token", "content": "str"}
    {"type": "tool_start", "tool_call_id": "str", "name": "str",
     "category": "file"|"exec"|"plan"|"web"|"app"|"other", "args": {}}
    {"type": "tool_end", "tool_call_id": "str", "name": "str",
     "status": "success"|"error", "result_preview": "str"}
    {"type": "approval_request", "interrupt_id": "str",
     "actions": [{"tool_call_id", "name", "category", "args", "description"}]}
     # M8-03: emitted instead of a normal completion when the turn ends with
     # one or more mutating tool calls paused for human approval; ALWAYS
     # immediately followed by `turn_end {"status": "awaiting_approval"}`
     # (no `turn_end {"status": "completed"}` for that turn).
    {"type": "turn_end", "status": "completed"|"cancelled"|"awaiting_approval",
     "duration_ms": int}   # M9-02: elapsed since this turn's `turn_start`
    {"type": "error", "message": "str"}   # then close, code 1011

Anything other than a well-formed `user_message` frame, received while idle
(i.e. NOT mid-turn, NOT awaiting approval) -> `error` frame, then close code
1008 (policy violation) — this excludes `{"type": "cancel"}`, which is a
no-op outside a turn (see `chat_ws` below) rather than a close. Any frame
received WHILE a turn is in flight is handled by `_watch_inbound` instead:
`{"type": "cancel"}` cancels the turn (see M8-01 notes below); an
`approval_response` is held until the turn ends and then handled as if it
had arrived next, but only if it names the interrupt that turn left
pending (#181: the client can answer `approval_request` before its
`turn_end`); every other frame received mid-turn, including a held
`approval_response` for any other interrupt, is ignored (looped past),
matching v1's existing "a well-behaved client never sends another frame
before turn_end/error" assumption. A `cancel` that lands after the turn
already paused (between `approval_request` and `turn_end`) can't un-pause
it: after the cancelled `turn_end` the still-pending approval is
re-announced (`approval_request` + `turn_end {"status":
"awaiting_approval"}`) unless the client already answered it (#189).
While AWAITING APPROVAL (i.e.
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
  `hitl_enabled` in `configurable` — read fresh from the connected user's
  `SettingsStore` document at the start of every turn
  (`_current_hitl_enabled` below), so a mid-conversation settings change
  takes effect on the very next turn.
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
  and matching each of `action_requests` (built in call order by
  `HumanInTheLoopMiddleware.after_model`, args copied from the call) to the
  first unclaimed mutating call with the same name and args. Not every
  mutating call need have interrupted: a routine's `allow_writes` mode
  (M17-05) stops only deletes.
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

## M17-01 detached turns

Turns run in `app.agent.turn_runner.TurnRunner`, not in this socket's
task. A disconnect mid-turn only detaches the socket: the turn finishes,
checkpoints, bumps `updated_at` and persists its tip as usual, and is
cancelled only after `AGENT_DETACHED_TURN_TIMEOUT_S` with no socket
attached. A socket connecting to a thread with a running turn follows it
(replay, see above) before reading its own frames; `cancel` from any
attached socket cancels it. The runner holds the per-thread lock for the
whole turn. The notes below that say "the turn task" mean the runner's.

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
  `tool_call_chunks`. M8-07: the same chunk may also carry
  `additional_kwargs["reasoning_content"]` (surfaced by
  `ReasoningChatOpenAI`); those emit a `reasoning` frame *before* any
  `token` frame from the same event. Reasoning is live-only — it is not
  written to `turn_stats` or the history DTO.
- Per-turn thinking (M8-07): `_run_turn` sets
  `configurable["thinking_enabled"]` from `SettingsStore` (same fresh
  per-turn read as `hitl_enabled`). `ReasoningChatOpenAI._default_params`
  turns that into
  `extra_body={"chat_template_kwargs": {"enable_thinking": <bool>}}`
  (configurable -> model kwargs, not `model.bind(...)`).
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

- (Until M10-04, connecting also auto-created the `threads` row; now the
  thread must already exist and belong to the caller — see below.)
- On each well-formed `user_message`, before running the turn: set the
  thread's title to the first 60 chars of the message IF the title is still
  the default `"New chat"` (a no-op otherwise) — see `_derive_title`.
  M8-04: skipped when `replace_from_message_id` is set (title is not
  re-derived on edit/resend/regenerate).
- After a turn ends (completed, cancelled or awaiting approval — NOT on an
  unhandled-error abort), whether or not its socket is still there: bump
  `updated_at = now()`.

## M8-04 edit / resend / regenerate (`replace_from_message_id`)

- Every fresh `HumanMessage` is constructed with `id=str(uuid4())` so user
  rows are addressable via `GET /api/threads/{id}/messages` (`MessageOut.id`
  is that stored LangChain id). The client may also send an `id` on the
  `user_message` frame; if it's a non-empty string, that value is used
  instead so a same-session Edit/Resend can address the bubble it just
  appended without waiting for a history refetch.
- Optional `replace_from_message_id` + `mode` (`"truncate"` | `"fork"`).
  Omitted `mode` falls back to `SettingsStore.edit_mode_default` (M8-02).
- `mode: "truncate"` (under the per-thread lock, BEFORE the new turn):
  `aget_state` at the thread's `active_checkpoint_id` (or latest if null),
  locate the message with that id (must be a `HumanMessage`, else `error`
  + 1008; unknown id is the same), then
  `aupdate_state(config, {"messages": [RemoveMessage(id=m.id) for m in
  messages[idx:]]})`. LangGraph's `add_messages` reducer honors
  `RemoveMessage`, so this is one call. Then the new `HumanMessage` runs
  normally from the post-truncate checkpoint.
- `mode: "fork"` (M8-05): walk `aget_state_history` along the active
  lineage to the checkpoint whose `messages` end just before the target
  `HumanMessage`, then run the new turn with that `checkpoint_id` in
  `configurable` (LangGraph time-travel fork). The old continuation
  stays as a sibling branch; the new tip becomes `active_checkpoint_id`.

## M10-04 authentication and ownership

The socket is accepted first, then authenticated from `X-HomeAI-Identity`
(set by Caddy's `forward_auth`, verified in `app/core/identity.py`), so a
failure can carry a close code a browser can see:

- no / invalid identity -> close `4401` (the client re-checks its session);
- the platform's JWKS unreachable -> close `1011`;
- `thread_id` unknown, not a UUID (Postgres), or another user's thread ->
  close `4404`, identical in all three cases; nothing is read from the
  checkpointer first.

Once past that, every turn, resume, and settings read on the connection is
for that thread and that user (settings are per user). A later
`DELETE`/re-own of the thread doesn't affect an already-open socket.

## M11-02 delegation

The agent's file tools act as the user through a delegation token
(`app/core/delegation.py`, docs/PLATFORM.md §4). The socket exchanges the
identity token it was opened with for one right after the thread check,
re-mints it at the start of every turn and approval resume, and a
background task keeps it fresh while the socket is open, since the
identity token (5 min) is long gone by then. It rides in
`configurable["delegation"]` as an object LangGraph doesn't persist, never
in messages, the prompt, or tool arguments.

- the platform refuses (session revoked or expired, user disabled) ->
  close `4401`, at connect or at the next turn / resume;
- the platform unreachable at connect -> close `1011`; at a turn start the
  turn goes ahead on the current delegation until it expires.

## M8-05 active branch

`threads.active_checkpoint_id` (null = chronological latest) is the tip
the user is looking at. `aget_state` without a checkpoint id returns the
newest checkpoint by id, which may be a sibling the user is not viewing,
so history + WS + pending-approval reads pass `checkpoint_id` when set.
Every turn that writes a checkpoint (completed / cancelled /
awaiting_approval) stores that run's new tip as `active_checkpoint_id`.
New turns (and approval resumes) start from the active tip so a message
typed on an old branch extends that branch.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
from collections.abc import Awaitable
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any, Literal
from uuid import uuid4

from fastapi import APIRouter
from langchain_core.messages import AIMessage, HumanMessage, RemoveMessage
from langgraph.errors import GraphInterrupt
from langgraph.types import Command, StateSnapshot
from starlette.websockets import WebSocket, WebSocketDisconnect

from app.agent.app_tools import APP_TOOL_NAMES
from app.agent.build import MUTATING_TOOL_NAMES
from app.agent.routine_tools import ROUTINE_TOOL_NAMES
from app.agent.turn_runner import END, ActiveTurn, TurnOutcome, TurnRequest, TurnRunner
from app.core.delegation import Delegation, DelegationDenied, DelegationUnavailable
from app.core.identity import IDENTITY_HEADER, IdentityError, KeysUnavailable
from app.db.rls import bind_user
from app.db.turn_stats import TurnStat

logger = logging.getLogger(__name__)

router = APIRouter()

# Exact category mapping from Conventions & Contracts §6.
_TOOL_CATEGORY_BY_NAME: dict[str, str] = {
    "ls": "file",
    "read_file": "file",
    "write_file": "file",
    "edit_file": "file",
    "outline": "file",
    "replace_symbol": "file",
    "glob": "file",
    "grep": "file",
    "delete": "file",
    "execute_code": "exec",
    "write_todos": "plan",
    "task": "plan",
    "web_search": "web",
    "web_fetch": "web",
    **dict.fromkeys(APP_TOOL_NAMES, "app"),
    **dict.fromkeys(ROUTINE_TOOL_NAMES, "plan"),
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


@dataclass(frozen=True)
class _ParsedUserMessage:
    """A well-formed inbound `user_message` frame (M8-04 fields optional)."""

    content: str
    replace_from_message_id: str | None = None
    mode: Literal["truncate", "fork"] | None = None
    message_id: str | None = None


def _parse_user_message(raw: object) -> _ParsedUserMessage | None:
    """Return a parsed `user_message` if `raw` is well-formed, else `None`.

    Required: `type == "user_message"` and `content` a `str`. Optional M8-04
    fields, when present, must have the right type/`Literal` or the whole
    frame is rejected (same 1008 path as any other invalid idle frame):
    `replace_from_message_id` a non-empty `str`, `mode` `"truncate"` or
    `"fork"`, `id` a non-empty `str` (client-supplied LangChain message id).
    """
    if not isinstance(raw, dict):
        return None
    if raw.get("type") != "user_message":
        return None
    content = raw.get("content")
    if not isinstance(content, str):
        return None

    replace_from = raw.get("replace_from_message_id")
    if replace_from is not None and not (isinstance(replace_from, str) and replace_from):
        return None

    mode = raw.get("mode")
    parsed_mode: Literal["truncate", "fork"] | None
    if mode is None:
        parsed_mode = None
    elif mode in ("truncate", "fork"):
        parsed_mode = mode
    else:
        return None

    message_id = raw.get("id")
    if message_id is not None and not (isinstance(message_id, str) and message_id):
        return None

    return _ParsedUserMessage(
        content=content,
        replace_from_message_id=replace_from,
        mode=parsed_mode,
        message_id=message_id,
    )


def _pending_approval_from_state(state: StateSnapshot) -> dict | None:
    """Derive the `approval_request`/`GET .../state` payload from checkpointed state.

    Returns `{"interrupt_id": str, "actions": [{"tool_call_id", "name",
    "category", "args", "description"}]}` or `None` if there's no pending
    interrupt. See the module docstring's M8-03 section for the full
    tool_call_id-recovery reasoning: `HumanInTheLoopMiddleware.after_model`
    raises exactly one `Interrupt` whose `value["action_requests"]` doesn't
    carry a `tool_call_id`, so each request is matched to the last
    `AIMessage`'s tool call with the same name and arguments (the
    middleware copies both from the call, in call order).

    M13-02: an app tool raises its own interrupt from inside the tool
    (`app/agent/app_tools.py`), carrying its `tool_call_id`, and parallel
    calls can leave several pending at once (one per tool task). Their
    actions are merged into one approval under the first interrupt's id;
    `_RESUME_KEY` records which actions belong to which interrupt so
    `_resume_command` can answer each (it's stripped by `public_approval`).
    """
    interrupts = [
        i
        for task in state.tasks
        for i in task.interrupts
        if isinstance(i.value, dict) and i.value.get("action_requests")
    ]
    if not interrupts:
        return None

    messages = state.values.get("messages", [])
    last_ai_msg = next((m for m in reversed(messages) if isinstance(m, AIMessage)), None)
    # Not every mutating call need have stopped (a routine's `allow_writes`
    # mode stops only deletes), so each request takes the first unclaimed
    # call with its name and arguments, in call order.
    unclaimed = [
        tc
        for tc in (last_ai_msg.tool_calls if last_ai_msg is not None else [])
        if tc["name"] in MUTATING_TOOL_NAMES
    ]

    def middleware_id(name: str, args: Any) -> str:
        for index, call in enumerate(unclaimed):
            if call["name"] == name and call.get("args") == args:
                return unclaimed.pop(index)["id"]
        return ""

    actions = []
    groups = []
    for interrupt in interrupts:
        action_requests = interrupt.value["action_requests"]
        for action_request in action_requests:
            name = action_request.get("name", "")
            tool_call_id = action_request.get("tool_call_id") or middleware_id(
                name, action_request.get("args")
            )
            actions.append(
                {
                    "tool_call_id": tool_call_id,
                    "name": name,
                    "category": _category_for_tool(name),
                    "args": _truncated_args(action_request.get("args")),
                    "description": action_request.get("description", ""),
                }
            )
        groups.append((str(interrupt.id), len(action_requests)))
    return {"interrupt_id": groups[0][0], "actions": actions, _RESUME_KEY: groups}


_RESUME_KEY = "_resume"


def public_approval(pending_approval: dict | None) -> dict | None:
    """The pending approval as clients see it (`GET .../state`, `approval_request`)."""
    if pending_approval is None:
        return None
    return {k: v for k, v in pending_approval.items() if k != _RESUME_KEY}


def _resume_command(pending_approval: dict, decisions: list[dict]) -> Command:
    """`decisions` (in `actions` order) as the resume for every pending interrupt."""
    groups = pending_approval.get(_RESUME_KEY) or [
        (pending_approval["interrupt_id"], len(decisions))
    ]
    if len(groups) == 1:
        return Command(resume={"decisions": decisions})
    resume, start = {}, 0
    for interrupt_id, count in groups:
        resume[interrupt_id] = {"decisions": decisions[start : start + count]}
        start += count
    return Command(resume=resume)


def graph_config(
    thread_id: str,
    checkpoint_id: str | None = None,
    *,
    hitl_enabled: bool | None = None,
    thinking_enabled: bool | None = None,
    delegation: Delegation | None = None,
    approval_mode: str | None = None,
    user_id: str | None = None,
) -> dict[str, Any]:
    """RunnableConfig for this thread, optionally pinned to a checkpoint (M8-05).

    `user_id` is the turn's user, for tools that act on their own records
    (the routine tools, M17-07).

    `approval_mode` (a routine run's, M17-05) is a string, so LangGraph also
    writes it into each checkpoint's metadata - which is how a resume from a
    chat (`paused_approval_mode`) answers under the mode the run paused in.
    """
    configurable: dict[str, Any] = {"thread_id": thread_id, "checkpoint_ns": ""}
    if checkpoint_id:
        configurable["checkpoint_id"] = checkpoint_id
    if hitl_enabled is not None:
        configurable["hitl_enabled"] = hitl_enabled
    if thinking_enabled is not None:
        configurable["thinking_enabled"] = thinking_enabled
    if delegation is not None:
        configurable["delegation"] = delegation
    if approval_mode is not None:
        configurable["approval_mode"] = approval_mode
    if user_id is not None:
        configurable["user_id"] = user_id
    return {"configurable": configurable}


async def paused_approval_mode(agent: Any, thread_id: str) -> str | None:
    """The approval mode the thread's pending approval was raised under (None: a chat's).

    The HITL middleware decides again on resume which calls needed approval,
    so a resume must run under the same mode or the decisions won't line up.
    """
    state = await agent.aget_state(graph_config(thread_id))
    mode = (state.metadata or {}).get("approval_mode")
    return mode if isinstance(mode, str) else None


def checkpoint_id_of(config_or_state: Any) -> str | None:
    """`configurable.checkpoint_id` on a `StateSnapshot` or config dict."""
    config = getattr(config_or_state, "config", config_or_state)
    if not isinstance(config, dict):
        return None
    value = (config.get("configurable") or {}).get("checkpoint_id")
    return value if isinstance(value, str) and value else None


async def list_state_history(agent: Any, thread_id: str) -> list[StateSnapshot]:
    """All checkpoints for `thread_id`, newest first (no `checkpoint_id` filter)."""
    return [s async for s in agent.aget_state_history(graph_config(thread_id))]


async def get_pending_approval(
    agent: Any, thread_id: str, checkpoint_id: str | None = None
) -> dict | None:
    """Public helper for `GET /api/threads/{id}/state` (`app/api/chat.py`).

    Re-derives the pending approval purely from the checkpointer's own
    state — no extra persistent storage needed (see module docstring).
    M8-05: pin to `checkpoint_id` when the thread has an active tip.
    """
    state = await agent.aget_state(graph_config(thread_id, checkpoint_id))
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


def _reasoning_content(chunk: Any) -> str:
    """Reasoning delta on an `AIMessageChunk`, or `""` if the chunk has none."""
    extra = getattr(chunk, "additional_kwargs", None) or {}
    if not isinstance(extra, dict):
        return ""
    value = extra.get("reasoning_content")
    return value if isinstance(value, str) else ""


def _frames_for_event(event: dict) -> list[dict]:
    """Map one `astream_events` event to zero or more outgoing frame dicts."""
    kind = event.get("event")
    data = event.get("data") or {}

    if kind == "on_chat_model_stream":
        chunk = data.get("chunk")
        frames: list[dict] = []
        reasoning = _reasoning_content(chunk)
        if reasoning:
            frames.append({"type": "reasoning", "content": reasoning})
        text = getattr(chunk, "text", "")
        if text:
            frames.append({"type": "token", "content": text})
        return frames

    if kind == "on_tool_start":
        name = event.get("name", "")
        return [
            {
                "type": "tool_start",
                "tool_call_id": str(event.get("run_id", "")),
                "name": name,
                "category": _category_for_tool(name),
                "args": _truncated_args(data.get("input")),
            }
        ]

    if kind == "on_tool_end":
        name = event.get("name", "")
        output = data.get("output")
        status = getattr(output, "status", "success")
        if status not in ("success", "error"):
            status = "success"
        return [
            {
                "type": "tool_end",
                "tool_call_id": str(event.get("run_id", "")),
                "name": name,
                "status": status,
                "result_preview": _tool_result_preview(output),
            }
        ]

    if kind == "on_tool_error":
        name = event.get("name", "")
        error = data.get("error")
        if isinstance(error, GraphInterrupt):
            # A tool paused itself for approval (M13-02); it reruns, with a new
            # `tool_start`, when the run resumes. The client drops the card
            # left running on `approval_request`.
            return []
        return [
            {
                "type": "tool_end",
                "tool_call_id": str(event.get("run_id", "")),
                "name": name,
                "status": "error",
                "result_preview": repr(error)[:_RESULT_PREVIEW_TRUNCATE_LEN],
            }
        ]

    return []


def _elapsed_ms(started_mono: float) -> int:
    return max(0, int((time.monotonic() - started_mono) * 1000))


def _last_assistant_id(state: StateSnapshot) -> str | None:
    messages = state.values.get("messages", [])
    last_ai = next((m for m in reversed(messages) if isinstance(m, AIMessage)), None)
    if last_ai is None:
        return None
    return last_ai.id if last_ai.id is not None else None


async def _persist_turn_stat(
    app_state: Any,
    thread_id: str,
    status: str,
    duration_ms: int,
    started_at: datetime,
    prior_assistant_id: str | None,
) -> None:
    """Write `turn_stats` for this turn's final assistant message, if any.

    Skips when the last checkpointed `AIMessage` is unchanged from
    `prior_assistant_id` — the usual cancelled-mid-stream case, where
    LangGraph never checkpoints the interrupted model node (see module
    docstring). A completed / awaiting_approval turn always has a new
    last assistant id to attach to.
    """
    store = getattr(app_state, "turn_stats_store", None)
    if store is None:
        return
    # After the turn we just wrote, chronological latest is this run's tip
    # (per-thread lock). Do not re-read with the *starting* checkpoint_id.
    state = await app_state.agent.aget_state(graph_config(thread_id))
    final_id = _last_assistant_id(state)
    if final_id is None or final_id == prior_assistant_id:
        return
    await store.upsert(
        TurnStat(
            thread_id=thread_id,
            final_message_id=final_id,
            status=status,
            duration_ms=duration_ms,
            started_at=started_at,
        )
    )


async def turn_end_frame(app_state: Any, turn: ActiveTurn, status: str) -> dict:
    """Record the turn's stats and build its `turn_end` frame."""
    duration_ms = _elapsed_ms(turn.started_mono)
    await _persist_turn_stat(
        app_state, turn.thread_id, status, duration_ms, turn.started_at, turn.prior_assistant_id
    )
    return {"type": "turn_end", "status": status, "duration_ms": duration_ms}


def approval_request_frame(pending_approval: dict) -> dict:
    return {
        "type": "approval_request",
        "interrupt_id": pending_approval["interrupt_id"],
        "actions": pending_approval["actions"],
    }


async def _announce_pending_approval(
    websocket: WebSocket, turn: ActiveTurn, pending_approval: dict
) -> None:
    await websocket.send_json(approval_request_frame(pending_approval))
    await websocket.send_json(
        await turn_end_frame(websocket.app.state, turn, "awaiting_approval")
    )


def _is_cancel_frame(raw: object) -> bool:
    return isinstance(raw, dict) and raw.get("type") == "cancel"


def _is_approval_response_frame(raw: object) -> bool:
    return isinstance(raw, dict) and raw.get("type") == "approval_response"


def _take_deferred_approval(deferred: list[dict], pending_approval: dict | None) -> dict | None:
    """Pop the `approval_response` buffered mid-turn for `pending_approval`, if any.

    Clears the buffer either way: a frame naming any other interrupt (a
    repeat click for one already answered) is dropped, as it always was.
    """
    match = None
    if pending_approval is not None:
        match = next(
            (f for f in deferred if f.get("interrupt_id") == pending_approval["interrupt_id"]),
            None,
        )
    deferred.clear()
    return match


async def _watch_inbound(websocket: WebSocket, deferred: list[dict]) -> str:
    """Block until either the client disconnects or sends a `cancel` frame.

    Returns `"disconnect"` or `"cancel"`. An `approval_response` is appended
    to `deferred` (#181: the client shows the approval card on
    `approval_request`, before this turn's `turn_end`); any other frame
    received mid-turn (well-formed or not) is ignored — looped past — rather
    than misinterpreted; v1 defines no other inbound behavior mid-turn.
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
        if _is_approval_response_frame(raw):
            deferred.append(raw)


async def _forward_frames(websocket: WebSocket, queue: asyncio.Queue) -> None:
    """Send frames up to and including the turn's last (`turn_end` or `error`)."""
    while (frame := await queue.get()) is not END:
        await websocket.send_json(frame)
        if frame["type"] in ("turn_end", "error"):
            return


async def _refresh_for_turn(delegation: Delegation) -> None:
    """M11-02: every turn and resume starts on a freshly minted delegation, so
    a revoked session stops here (`DelegationDenied`, before `turn_start`).
    If the platform can't be asked, the turn runs on the current one while
    it lasts."""
    try:
        await delegation.refresh()
    except DelegationUnavailable as exc:
        if delegation.token is None:
            raise
        logger.warning("delegation: refresh at turn start failed: %s", exc)


async def _follow_turn(
    websocket: WebSocket, turn: ActiveTurn, queue: asyncio.Queue, deferred: list[dict]
) -> TurnOutcome | None:
    """Send `turn`'s frames from `queue` until it ends, racing `_watch_inbound`.

    Returns the turn's outcome, or None if the client disconnected — the
    turn itself carries on detached (M17-01). A `cancel` frame cancels the
    turn; its `turn_end {"status": "cancelled"}` still arrives through
    `queue`. #189: the cancel can land after the turn already paused on an
    interrupt (between `approval_request` and its `turn_end`), so a still
    pending approval is re-announced (`approval_request` + `turn_end
    {"status": "awaiting_approval"}`) unless the client already answered it
    mid-turn, in which case the caller applies that held answer instead.
    `approval_response` frames received mid-turn are appended to `deferred`
    (see `_take_deferred_approval`).
    """
    forward = asyncio.create_task(_forward_frames(websocket, queue))
    watch = asyncio.create_task(_watch_inbound(websocket, deferred))
    try:
        done, _pending = await asyncio.wait({forward, watch}, return_when=asyncio.FIRST_COMPLETED)
        if watch in done:
            if watch.result() == "disconnect":
                return None
            turn.cancel()
            await forward
            outcome = await turn.wait()
            pending = outcome.pending_approval
            if (
                outcome.status == "cancelled"
                and pending is not None
                and not any(f.get("interrupt_id") == pending["interrupt_id"] for f in deferred)
            ):
                await _announce_pending_approval(websocket, turn, pending)
            return outcome
        # Stop reading before the client's next frame can arrive: it must
        # reach `_serve`, not this turn's watcher.
        watch.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await watch
        try:
            forward.result()
        except (WebSocketDisconnect, RuntimeError):
            return None
        return await turn.wait()
    except (WebSocketDisconnect, RuntimeError):
        return None
    finally:
        turn.detach(queue)
        for task in (forward, watch):
            if not task.done():
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await task


async def _send_error_and_close(websocket: WebSocket, message: str, code: int) -> None:
    with contextlib.suppress(Exception):
        await websocket.send_json({"type": "error", "message": message})
    with contextlib.suppress(Exception):
        await websocket.close(code=code)


async def _current_hitl_enabled(websocket: WebSocket, user_id: str) -> bool:
    """Fresh per-turn read of `SettingsStore.get_document().hitl_enabled` (M8-03).

    Read at the START of every turn (fresh AND resumed) rather than cached
    for the connection's lifetime, so a mid-conversation settings change
    (`PUT /api/settings`) takes effect on the very next turn.
    """
    settings_store = websocket.app.state.settings_store
    document = await settings_store.get_document(user_id)
    return document.hitl_enabled


async def _current_thinking_enabled(websocket: WebSocket, user_id: str) -> bool:
    """Fresh per-turn read of `SettingsStore.get_document().thinking_enabled` (M8-07).

    Same "read at the start of every turn" rule as `_current_hitl_enabled`.
    Default is `False` (`SettingsDocument.thinking_enabled`).
    """
    settings_store = websocket.app.state.settings_store
    document = await settings_store.get_document(user_id)
    return document.thinking_enabled


async def _truncate_from_message(
    agent: Any, thread_id: str, message_id: str, checkpoint_id: str | None
) -> tuple[str | None, str | None]:
    """Drop checkpointed messages from `message_id` onward (M8-04 truncate).

    Returns `(error, new_checkpoint_id)`. `error` is sent as `error` +
    close 1008 (unknown id, or the id is not a `HumanMessage`).
    `new_checkpoint_id` is the post-truncate tip to run the new turn from.
    Must be called while holding the per-thread lock.
    """
    config = graph_config(thread_id, checkpoint_id)
    state = await agent.aget_state(config)
    messages = state.values.get("messages", [])
    idx = next((i for i, m in enumerate(messages) if getattr(m, "id", None) == message_id), None)
    if idx is None:
        return f"unknown message id: {message_id}", None
    if not isinstance(messages[idx], HumanMessage):
        return "replace_from_message_id must refer to a user message", None
    removals = [RemoveMessage(id=m.id) for m in messages[idx:] if getattr(m, "id", None)]
    if removals:
        new_config = await agent.aupdate_state(config, {"messages": removals})
        return None, checkpoint_id_of(new_config)
    return None, checkpoint_id or checkpoint_id_of(state)


async def _find_fork_checkpoint_id(
    agent: Any, thread_id: str, message_id: str, active_checkpoint_id: str | None
) -> tuple[str | None, str | None]:
    """Walk the active lineage to the checkpoint just before `message_id`.

    Returns `(parent_checkpoint_id, error)` — same error strings as
    truncate (unknown id / not a user message).
    """
    state = await agent.aget_state(graph_config(thread_id, active_checkpoint_id))
    messages = state.values.get("messages", [])
    target = next((m for m in messages if getattr(m, "id", None) == message_id), None)
    if target is None:
        return None, f"unknown message id: {message_id}"
    if not isinstance(target, HumanMessage):
        return None, "replace_from_message_id must refer to a user message"

    snapshots = await list_state_history(agent, thread_id)
    by_id: dict[str, StateSnapshot] = {}
    parent_of: dict[str, str | None] = {}
    for snap in snapshots:
        cid = checkpoint_id_of(snap)
        if not cid:
            continue
        by_id[cid] = snap
        parent_of[cid] = checkpoint_id_of(snap.parent_config) if snap.parent_config else None

    current = active_checkpoint_id if active_checkpoint_id in by_id else checkpoint_id_of(state)
    seen: set[str] = set()
    while current and current not in seen:
        seen.add(current)
        snap = by_id.get(current)
        if snap is None:
            break
        msgs = snap.values.get("messages", [])
        ids = {getattr(m, "id", None) for m in msgs}
        # Skip mid-step snapshots (`next` non-empty). Starting from
        # `__start__` of the original turn would replay the pending write
        # that adds the target HumanMessage, so the fork would keep it.
        if message_id not in ids and not snap.next:
            return current, None
        current = parent_of.get(current)
    return None, f"unknown message id: {message_id}"


async def persist_active_tip(thread_store: Any, agent: Any, thread_id: str) -> str | None:
    """Store the chronological-latest checkpoint as this thread's active tip."""
    state = await agent.aget_state(graph_config(thread_id))
    tip = checkpoint_id_of(state)
    if tip:
        await thread_store.set_active_checkpoint_id(thread_id, tip)
    return tip


async def active_checkpoint_id_for(
    thread_store: Any, thread_id: str, owner_user_id: str
) -> str | None:
    record = await thread_store.get(thread_id, owner_user_id)
    return record.active_checkpoint_id if record is not None else None


WS_CLOSE_UNAUTHORIZED = 4401
WS_CLOSE_NOT_FOUND = 4404


@router.websocket("/ws/chat/{thread_id}")
async def chat_ws(websocket: WebSocket, thread_id: str) -> None:
    await websocket.accept()

    try:
        identity = await websocket.app.state.identity_verifier.verify(
            websocket.headers.get(IDENTITY_HEADER)
        )
    except KeysUnavailable:
        await websocket.close(code=1011, reason="identity keys unavailable")
        return
    except IdentityError:
        await websocket.close(code=WS_CLOSE_UNAUTHORIZED, reason="unauthenticated")
        return
    user_id = identity.user_id
    bind_user(user_id)

    thread_store = websocket.app.state.thread_store
    record = await thread_store.get(thread_id, user_id)
    if record is None:
        await websocket.close(code=WS_CLOSE_NOT_FOUND, reason="thread not found")
        return

    try:
        delegation = await Delegation.obtain(
            websocket.app.state.delegation_client,
            websocket.headers.get(IDENTITY_HEADER) or "",
            thread_id,
        )
    except DelegationDenied:
        await websocket.close(code=WS_CLOSE_UNAUTHORIZED, reason="unauthenticated")
        return
    except DelegationUnavailable:
        await websocket.close(code=1011, reason="platform unavailable")
        return

    keep_alive = asyncio.create_task(delegation.keep_alive())
    try:
        await _serve(websocket, thread_id, user_id, record, delegation)
    finally:
        keep_alive.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await keep_alive


async def _start_turn(
    websocket: WebSocket,
    runner: TurnRunner,
    request: TurnRequest,
    start_from: Awaitable[tuple[str | None, str | None]],
) -> tuple[ActiveTurn, asyncio.Queue] | None:
    """Under the thread's lock, resolve where the turn starts (`start_from`:
    `(error, checkpoint_id)`, an error closing 1008), then hand the lock to
    the runner. None: the socket was closed."""
    lock = runner.lock_for(request.thread_id)
    await lock.acquire()
    started = False
    try:
        error, checkpoint_id = await start_from
        if error is not None:
            await _send_error_and_close(websocket, error, code=1008)
            return None
        if request.delegation is not None:
            await _refresh_for_turn(request.delegation)
        result = await runner.start(replace(request, checkpoint_id=checkpoint_id), lock_held=True)
        started = True
        return result
    except DelegationDenied:
        await websocket.close(code=WS_CLOSE_UNAUTHORIZED, reason="unauthenticated")
        return None
    except Exception as exc:  # noqa: BLE001 - spec: any unhandled turn error -> `error` frame + close 1011
        await _send_error_and_close(websocket, str(exc), code=1011)
        return None
    finally:
        if not started:
            lock.release()


async def _track_routine_run(
    app_state: Any, thread_id: str, user_id: str, turn: ActiveTurn
) -> None:
    """A routine run's approval answered here: its run record follows the turn (M17-05)."""
    run = await app_state.routine_store.run_for_thread(thread_id, user_id)
    if run is not None and run.status == "waiting_approval":
        app_state.routine_scheduler.track_resumed(run, turn)


async def _active_tip(
    thread_store: Any, thread_id: str, user_id: str
) -> tuple[str | None, str | None]:
    return None, await active_checkpoint_id_for(thread_store, thread_id, user_id)


async def _edit_start(
    agent: Any,
    thread_store: Any,
    thread_id: str,
    user_id: str,
    replace_from_message_id: str | None,
    mode: Literal["truncate", "fork"] | None,
) -> tuple[str | None, str | None]:
    """Where a `user_message` turn starts: the active tip, or for an edit the
    fork parent / truncated tip (M8-04/M8-05)."""
    start_checkpoint = await active_checkpoint_id_for(thread_store, thread_id, user_id)
    if replace_from_message_id is None:
        return None, start_checkpoint
    if mode == "fork":
        fork_id, error = await _find_fork_checkpoint_id(
            agent, thread_id, replace_from_message_id, start_checkpoint
        )
        return error, fork_id
    return await _truncate_from_message(agent, thread_id, replace_from_message_id, start_checkpoint)


async def _serve(
    websocket: WebSocket, thread_id: str, user_id: str, record: Any, delegation: Delegation
) -> None:
    thread_store = websocket.app.state.thread_store
    runner: TurnRunner = websocket.app.state.turn_runner
    detached_timeout_s = websocket.app.state.settings.agent_detached_turn_timeout_s
    # M8-03: local routing state for this connection only — `None` while
    # idle/mid-turn, set to the pending approval right after a
    # `turn_end {"status": "awaiting_approval"}`. See module docstring's
    # M8-03 section: this is NOT the source of truth (a reconnect re-derives
    # it from the checkpointer via `GET /api/threads/{id}/state`). A fresh
    # socket must still *accept* `approval_response`/`cancel` for an
    # already-pending interrupt, so we hydrate this routing aid from the
    # checkpointer on connect rather than starting at `None` and treating
    # a legitimate resume as an invalid idle-frame (1008).
    pending_approval: dict | None = await get_pending_approval(
        websocket.app.state.agent, thread_id, record.active_checkpoint_id
    )
    deferred_approvals: list[dict] = []

    async def follow(turn: ActiveTurn, queue: asyncio.Queue) -> bool:
        """Follow a turn to its end; False once this connection is done."""
        nonlocal pending_approval
        outcome = await _follow_turn(websocket, turn, queue, deferred_approvals)
        if outcome is None:
            return False
        if outcome.status == "error":
            # Any other error already reached the client as an `error` frame.
            if isinstance(outcome.error, DelegationDenied):
                await websocket.close(code=WS_CLOSE_UNAUTHORIZED, reason="unauthenticated")
            else:
                with contextlib.suppress(Exception):
                    await websocket.close(code=1011)
            return False
        pending_approval = outcome.pending_approval
        return True

    # M17-01: a turn that outlived its socket (or runs for another one) is
    # replayed from its `turn_start`, then followed live.
    running = runner.active(thread_id)
    if running is not None and not await follow(running, running.attach()):
        return

    while True:
        raw = _take_deferred_approval(deferred_approvals, pending_approval)
        if raw is None:
            try:
                raw = await websocket.receive_json()
            except WebSocketDisconnect:
                return
            except (json.JSONDecodeError, UnicodeDecodeError, KeyError, TypeError):
                await _send_error_and_close(
                    websocket, "invalid frame: expected JSON text", code=1008
                )
                return

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

            resume = TurnRequest(
                thread_id=thread_id,
                user_id=user_id,
                run_input=_resume_command(pending_approval, decisions),
                hitl_enabled=await _current_hitl_enabled(websocket, user_id),
                thinking_enabled=await _current_thinking_enabled(websocket, user_id),
                delegation=delegation,
                detached_timeout_s=detached_timeout_s,
                approval_mode=await paused_approval_mode(websocket.app.state.agent, thread_id),
            )
            started = await _start_turn(
                websocket, runner, resume, _active_tip(thread_store, thread_id, user_id)
            )
            if started is not None:
                await _track_routine_run(websocket.app.state, thread_id, user_id, started[0])
            if started is None or not await follow(*started):
                return
            continue

        # M8-01: `cancel` received while idle (no turn in flight, no pending
        # approval) is a no-op — ignored, not a validation error, not a
        # close. Checked before `_parse_user_message` so it never falls
        # through to the invalid-frame/1008 path below.
        if _is_cancel_frame(raw):
            continue

        parsed = _parse_user_message(raw)
        if parsed is None:
            await _send_error_and_close(
                websocket,
                'invalid frame: expected {"type": "user_message", "content": <str>}',
                code=1008,
            )
            return

        settings_store = websocket.app.state.settings_store
        document = await settings_store.get_document(user_id)

        # M8-04/M8-05: `mode` is only meaningful with `replace_from_message_id`.
        # Omitted mode falls back to `edit_mode_default`.
        mode: Literal["truncate", "fork"] | None = None
        if parsed.replace_from_message_id is not None:
            mode = parsed.mode or document.edit_mode_default
        else:
            # New message at the end of the thread — title auto-set still
            # applies. Edit/resend/regenerate deliberately skip this.
            await thread_store.set_title_if_new(thread_id, _derive_title(parsed.content))

        human = HumanMessage(id=parsed.message_id or str(uuid4()), content=parsed.content)

        turn_request = TurnRequest(
            thread_id=thread_id,
            user_id=user_id,
            run_input={"messages": [human]},
            hitl_enabled=document.hitl_enabled,
            thinking_enabled=document.thinking_enabled,
            delegation=delegation,
            user_message={"id": human.id, "content": parsed.content},
            detached_timeout_s=detached_timeout_s,
        )
        start_from = _edit_start(
            websocket.app.state.agent,
            thread_store,
            thread_id,
            user_id,
            parsed.replace_from_message_id,
            mode,
        )
        started = await _start_turn(websocket, runner, turn_request, start_from)
        if started is None or not await follow(*started):
            return

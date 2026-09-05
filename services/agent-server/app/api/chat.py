"""REST thread management + history hydration — `/api/threads*`.

Contract fixed by docs/ARCHITECTURE.md's "Contracts" section ("Threads") —
do not deviate. This carries create/list/delete + history hydration on
page load / thread switch; the WS (`app/api/chat_ws.py`) only carries live
turns.

## Message-normalization introspection notes (real, not guessed — installed
`langchain-core==1.6.1` / `langgraph==1.2.11`; see this ticket's final report
for the full transcript):

- `agent.aget_state({"configurable": {"thread_id": ...}})` returns a
  `StateSnapshot`. For a thread id with NO checkpoint yet (row exists, no
  turns run), `state.values == {}` (an empty dict, not e.g. `{"messages":
  []}`) — confirmed by calling it against a fresh `thread_id` no turn had
  ever used. Hence `state.values.get("messages", [])` rather than
  `state.values["messages"]` directly.
- Real `state.values["messages"]` entries observed for a
  write_file-tool-call turn: `HumanMessage(content='write a file', id=...)`,
  `AIMessage(content='', tool_calls=[{'name': 'write_file', 'args': {...},
  'id': 'call_1', 'type': 'tool_call'}], id=...)`,
  `ToolMessage(content='Updated file /x.txt', name='write_file',
  tool_call_id='call_1', id=...)`, `AIMessage(content='done', tool_calls=[],
  id=...)`. No `SystemMessage` appears in state (deepagents/the model client
  supplies the system prompt out-of-band per call, it's never persisted into
  graph state) — the spec's "skip system messages" is handled defensively
  below anyway in case that ever changes.
- Every message had a non-`None` `.id` in practice: LangGraph's `messages`
  reducer (`langgraph.graph.message.add_messages`) auto-assigns a `uuid4`
  `.id` to any message that lacks one when merging into state. `BaseMessage`
  itself still types `.id` as `str | None`, so `_msg_id` below falls back to
  a fresh uuid4 defensively rather than assuming this always holds.
- `AIMessage.tool_calls` entries are `langchain_core.messages.tool.ToolCall`
  `TypedDict`s with keys `name`, `args`, `id`, and `type` (always the literal
  `"tool_call"`) — one extra key (`type`) beyond this ticket's DTO shape
  (`{"id", "name", "args"}`), dropped by only reading the three we want.
- `.text` (a property on every `BaseMessage`, not just the chunk classes
  used in `chat_ws.py`) normalizes both plain-string and content-block-list
  `.content` shapes to a plain `str` (it returns a `str` subclass,
  `TextAccessor`) — used here (as in `chat_ws.py`) instead of raw `.content`
  so a future content-block-shaped model response wouldn't need any changes
  to this normalization.
"""

from __future__ import annotations

import uuid
from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage
from langgraph.types import StateSnapshot
from pydantic import BaseModel

from app.api.chat_ws import (
    active_checkpoint_id_for,
    checkpoint_id_of,
    get_pending_approval,
    graph_config,
    list_state_history,
)
from app.db.threads import ThreadRecord, ThreadStore
from app.db.turn_stats import TurnStat, TurnStatsStore

router = APIRouter()


class CreateThreadBody(BaseModel):
    title: str | None = None


class ThreadOut(BaseModel):
    id: str
    title: str
    created_at: str
    updated_at: str


class ToolCallOut(BaseModel):
    id: str
    name: str
    args: dict


class TurnOut(BaseModel):
    """M9-02: per-turn status + duration, attached to the final assistant row."""

    status: Literal["completed", "cancelled", "awaiting_approval"]
    duration_ms: int


class BranchOut(BaseModel):
    checkpoint_id: str
    preview: str
    created_at: str | None = None


class BranchPointOut(BaseModel):
    """One fork on the active lineage with more than one child (M8-05)."""

    anchor_message_id: str
    branches: list[BranchOut]
    active_index: int


class ActiveBranchBody(BaseModel):
    checkpoint_id: str


class MessageOut(BaseModel):
    id: str
    role: Literal["user", "assistant", "tool"]
    content: str
    tool_name: str | None = None
    tool_calls: list[ToolCallOut] | None = None
    # M8-04: on `tool` rows, the paired `AIMessage.tool_calls[].id` so the
    # frontend can recover `args` from that assistant row (the known
    # `args: {}` gap in `mapHistoryToItems`). `None` on user/assistant rows.
    tool_call_id: str | None = None
    # M9-02: present on the final assistant row of a turn that has a
    # `turn_stats` row keyed by this message's id. `None` everywhere else.
    turn: TurnOut | None = None


def _thread_store(request: Request) -> ThreadStore:
    return request.app.state.thread_store


def _turn_stats_store(request: Request) -> TurnStatsStore | None:
    return getattr(request.app.state, "turn_stats_store", None)


def _turn_out_from_stat(stat: TurnStat) -> TurnOut | None:
    if stat.status not in ("completed", "cancelled", "awaiting_approval"):
        return None
    return TurnOut(status=stat.status, duration_ms=stat.duration_ms)


def _to_thread_out(record: ThreadRecord) -> ThreadOut:
    return ThreadOut(
        id=record.id,
        title=record.title,
        created_at=record.created_at.isoformat(),
        updated_at=record.updated_at.isoformat(),
    )


def _msg_id(message: BaseMessage) -> str:
    # See module docstring: real messages always carry an id in practice
    # (LangGraph's `add_messages` reducer auto-assigns one), but `.id` is
    # still typed `str | None` — fall back defensively rather than emit a
    # `null` DTO id.
    return message.id if message.id is not None else str(uuid.uuid4())


def _normalize_message(message: BaseMessage) -> MessageOut | None:
    """Map one checkpointed LangChain message to the §5 message DTO, or `None` to skip it."""
    if isinstance(message, SystemMessage):
        return None
    if isinstance(message, HumanMessage):
        return MessageOut(id=_msg_id(message), role="user", content=str(message.text))
    if isinstance(message, AIMessage):
        tool_calls = [
            ToolCallOut(id=tc.get("id") or "", name=tc["name"], args=tc.get("args") or {})
            for tc in message.tool_calls
        ]
        return MessageOut(
            id=_msg_id(message),
            role="assistant",
            content=str(message.text),
            tool_calls=tool_calls or None,
        )
    if isinstance(message, ToolMessage):
        return MessageOut(
            id=_msg_id(message),
            role="tool",
            content=str(message.text),
            tool_name=message.name,
            tool_call_id=message.tool_call_id or None,
        )
    # Unknown/future message type: skip rather than raise, since silently
    # dropping an unrecognized message from history is safer than a 500 on
    # every subsequent `GET .../messages` call for the thread.
    return None


@router.post("/threads", status_code=201, response_model=ThreadOut)
async def create_thread(request: Request, body: CreateThreadBody | None = None) -> ThreadOut:
    store = _thread_store(request)
    title = body.title if body is not None else None
    record = await store.create(title)
    return _to_thread_out(record)


@router.get("/threads", response_model=list[ThreadOut])
async def list_threads(request: Request) -> list[ThreadOut]:
    store = _thread_store(request)
    records = await store.list_all()
    return [_to_thread_out(r) for r in records]


@router.get("/threads/{thread_id}/messages", response_model=list[MessageOut])
async def get_thread_messages(thread_id: str, request: Request) -> list[MessageOut]:
    store = _thread_store(request)
    record = await store.get(thread_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"thread '{thread_id}' not found")

    agent = request.app.state.agent
    state = await agent.aget_state(graph_config(thread_id, record.active_checkpoint_id))
    # See module docstring: `state.values` is `{}` (not `{"messages": []}`)
    # when the row exists but no checkpoint has been written yet.
    messages = state.values.get("messages", [])

    normalized = [m for m in (_normalize_message(m) for m in messages) if m is not None]

    store = _turn_stats_store(request)
    if store is None or not normalized:
        return normalized
    stats = await store.list_for_thread(thread_id)
    by_final_id = {s.final_message_id: s for s in stats}
    attached: list[MessageOut] = []
    for message in normalized:
        stat = by_final_id.get(message.id)
        if stat is not None and message.role == "assistant":
            turn = _turn_out_from_stat(stat)
            if turn is not None:
                message = message.model_copy(update={"turn": turn})
        attached.append(message)
    return attached


@router.get("/threads/{thread_id}/state")
async def get_thread_state(thread_id: str, request: Request) -> dict:
    """`GET /api/threads/{id}/state` -> `{"pending_approval": {...} | null}` (M8-03).

    Same shape as the `approval_request` frame's payload minus the frame's
    own `type` envelope. `useChat` (frontend) calls this once after history
    hydration on (re)connect so a pending approval survives a page reload —
    derived purely from the checkpointer's own interrupt state
    (`app.api.chat_ws.get_pending_approval`), no extra persistent storage.

    Deliberately does NOT 404 for an unknown `thread_id` (unlike `GET
    .../messages`): a thread with no checkpoint yet trivially has no
    pending approval, and this endpoint's only caller polls it
    unconditionally on every connect/reconnect, thread-existence
    already-checked-elsewhere included.
    """
    store = _thread_store(request)
    checkpoint_id = await active_checkpoint_id_for(store, thread_id)
    agent = request.app.state.agent
    pending_approval = await get_pending_approval(agent, thread_id, checkpoint_id)
    return {"pending_approval": pending_approval}


def _build_branch_points(
    snapshots: list[StateSnapshot], active_checkpoint_id: str | None
) -> list[BranchPointOut]:
    """One entry per active-lineage node with more than one child checkpoint.

    `branches[].checkpoint_id` is the tip of that child subtree (the active
    tip when the child is on the active lineage, otherwise the newest tip
    of the sibling). `anchor_message_id` is the first `HumanMessage` after
    the fork point on the *active* lineage — the user bubble that shows
    `‹ 1/2 ›`.
    """
    if not snapshots:
        return []

    by_id: dict[str, StateSnapshot] = {}
    children: dict[str, list[str]] = {}
    for snap in snapshots:
        cid = checkpoint_id_of(snap)
        if not cid:
            continue
        by_id[cid] = snap
        parent_id = checkpoint_id_of(snap.parent_config) if snap.parent_config else None
        if parent_id:
            children.setdefault(parent_id, []).append(cid)

    if active_checkpoint_id and active_checkpoint_id in by_id:
        active_tip = active_checkpoint_id
    else:
        active_tip = next((cid for s in snapshots if (cid := checkpoint_id_of(s))), None)
    if active_tip is None:
        return []

    lineage: list[str] = []
    current: str | None = active_tip
    seen: set[str] = set()
    while current and current not in seen and current in by_id:
        seen.add(current)
        lineage.append(current)
        parent_cfg = by_id[current].parent_config
        current = checkpoint_id_of(parent_cfg) if parent_cfg else None
    lineage_set = set(lineage)

    def tip_of_subtree(start: str) -> str:
        node = start
        for _ in range(10_000):
            kids = children.get(node, [])
            if not kids:
                return node
            on_active = [k for k in kids if k in lineage_set]
            if on_active:
                node = on_active[0]
                continue
            node = max(kids, key=lambda k: (by_id[k].created_at or "", k))
        return node

    points: list[BranchPointOut] = []
    for parent_id in lineage:
        kids = children.get(parent_id, [])
        if len(kids) <= 1:
            continue
        parent_msg_ids = {
            getattr(m, "id", None) for m in by_id[parent_id].values.get("messages", [])
        }
        branches: list[BranchOut] = []
        for child_id in kids:
            tip_id = tip_of_subtree(child_id)
            tip = by_id[tip_id]
            first_new_human = next(
                (
                    m
                    for m in tip.values.get("messages", [])
                    if isinstance(m, HumanMessage) and getattr(m, "id", None) not in parent_msg_ids
                ),
                None,
            )
            preview = str(first_new_human.text) if first_new_human is not None else ""
            branches.append(
                BranchOut(
                    checkpoint_id=tip_id,
                    preview=preview,
                    created_at=by_id[child_id].created_at,
                )
            )
        branches.sort(key=lambda b: (b.created_at or "", b.checkpoint_id))

        anchor = next(
            (
                m
                for m in by_id[active_tip].values.get("messages", [])
                if isinstance(m, HumanMessage) and getattr(m, "id", None) not in parent_msg_ids
            ),
            None,
        )
        if anchor is None or not getattr(anchor, "id", None):
            continue
        active_index = next(
            (i for i, b in enumerate(branches) if b.checkpoint_id == active_tip), 0
        )
        points.append(
            BranchPointOut(
                anchor_message_id=anchor.id,
                branches=branches,
                active_index=active_index,
            )
        )

    points.reverse()
    return points


@router.get("/threads/{thread_id}/branches", response_model=list[BranchPointOut])
async def get_thread_branches(thread_id: str, request: Request) -> list[BranchPointOut]:
    """`GET /api/threads/{id}/branches` (M8-05).

    One entry per point on the active lineage that has more than one child
    branch, computed from `aget_state_history` parent links.
    """
    store = _thread_store(request)
    record = await store.get(thread_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"thread '{thread_id}' not found")

    agent = request.app.state.agent
    snapshots = await list_state_history(agent, thread_id)
    return _build_branch_points(snapshots, record.active_checkpoint_id)


@router.put("/threads/{thread_id}/active_branch", status_code=204)
async def set_active_branch(thread_id: str, request: Request, body: ActiveBranchBody) -> None:
    """`PUT /api/threads/{id}/active_branch` `{checkpoint_id}` (M8-05).

    Sets the thread's active tip. 404 if `checkpoint_id` is not a tip of
    this thread (unknown id, or it has children).
    """
    store = _thread_store(request)
    record = await store.get(thread_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"thread '{thread_id}' not found")

    agent = request.app.state.agent
    snapshots = await list_state_history(agent, thread_id)
    children: dict[str, list[str]] = {}
    known: set[str] = set()
    for snap in snapshots:
        cid = checkpoint_id_of(snap)
        if not cid:
            continue
        known.add(cid)
        parent_id = checkpoint_id_of(snap.parent_config) if snap.parent_config else None
        if parent_id:
            children.setdefault(parent_id, []).append(cid)

    if body.checkpoint_id not in known or children.get(body.checkpoint_id):
        raise HTTPException(
            status_code=404,
            detail=f"checkpoint '{body.checkpoint_id}' is not a tip of this thread",
        )
    await store.set_active_checkpoint_id(thread_id, body.checkpoint_id)


@router.delete("/threads/{thread_id}", status_code=204)
async def delete_thread(thread_id: str, request: Request) -> None:
    store = _thread_store(request)
    await store.delete(thread_id)
    turn_stats = _turn_stats_store(request)
    if turn_stats is not None:
        await turn_stats.delete_for_thread(thread_id)
    # `BaseCheckpointSaver.adelete_thread` is a real, non-abstract method on
    # both `AsyncPostgresSaver` (confirmed via
    # `inspect.getsource(AsyncPostgresSaver.adelete_thread)` — deletes from
    # `checkpoints`/`checkpoint_blobs`/`checkpoint_writes` by `thread_id`,
    # per this ticket's final report) and `MemorySaver` (delegates to its own
    # sync `delete_thread`) — no version-gap fallback path is needed, and
    # both are idempotent (deleting an unknown/never-used thread_id is a
    # normal zero-row no-op, not an error), matching this endpoint's
    # idempotent-204 contract.
    checkpointer = request.app.state.checkpointer
    await checkpointer.adelete_thread(thread_id)

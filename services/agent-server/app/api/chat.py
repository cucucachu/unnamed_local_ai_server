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
from pydantic import BaseModel

from app.db.threads import ThreadRecord, ThreadStore

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


class MessageOut(BaseModel):
    id: str
    role: Literal["user", "assistant", "tool"]
    content: str
    tool_name: str | None = None
    tool_calls: list[ToolCallOut] | None = None


def _thread_store(request: Request) -> ThreadStore:
    return request.app.state.thread_store


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
            id=_msg_id(message), role="tool", content=str(message.text), tool_name=message.name
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
    state = await agent.aget_state({"configurable": {"thread_id": thread_id}})
    # See module docstring: `state.values` is `{}` (not `{"messages": []}`)
    # when the row exists but no checkpoint has been written yet.
    messages = state.values.get("messages", [])

    normalized = (_normalize_message(m) for m in messages)
    return [m for m in normalized if m is not None]


@router.delete("/threads/{thread_id}", status_code=204)
async def delete_thread(thread_id: str, request: Request) -> None:
    store = _thread_store(request)
    await store.delete(thread_id)
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

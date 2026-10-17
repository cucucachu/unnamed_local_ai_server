"""`list_chats` and `read_chat`: the user's past chats, as conversation (M21-05, docs/MEMORY.md §6).

Both read agent-server's own records (the thread store, the checkpointer,
`turn_stats`) for the user the run acts as (`configurable["user_id"]`), so
they only ever see that user's chats; row-level security (`app.db.rls`)
enforces the same underneath. A chat reads as turns: the user's message and
the agent's final reply. Tool calls, tool results, reasoning and the text
between tool calls are left out: they are long, they can hold other
people's data (a shared space, an integration), and the conversation is
what's worth remembering.

Times are shown, and naive times read, in the user's timezone
(`SettingsDocument.timezone`), like `current_time`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time
from typing import Any

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool, tool

from app.agent.routine_tools import _user_id, _user_timezone
from app.db.threads import ThreadRecord
from app.routines import schedule as sched

CHAT_TOOL_NAMES: tuple[str, ...] = ("list_chats", "read_chat")
PAGE_CHARS = 12_000
MAX_LIST = 100
_NO_USER = "Error: chats are unavailable for this run."


def _not_found(chat_id: str) -> str:
    return f"Error: chat {chat_id} not found. Call list_chats for the ids."


@dataclass(frozen=True)
class Turn:
    at: datetime
    user: str
    reply: str


def parse_time(value: str, timezone: str) -> datetime:
    """An ISO date or date-time; one without an offset is in `timezone`."""
    text = value.strip()
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise ValueError(
            f"Error: {value!r} isn't a date or time; use e.g. 2026-10-17 or 2026-10-17T18:00."
        ) from exc
    if len(text) == 10:
        parsed = datetime.combine(parsed.date(), time())
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=sched.zone(timezone))
    return parsed


def turns(
    messages: list[BaseMessage], stamps: dict[str, datetime], default: datetime
) -> list[Turn]:
    """Each user message with the last text-only assistant message before the next one."""
    out: list[Turn] = []
    user: HumanMessage | None = None
    final: AIMessage | None = None

    def close() -> None:
        if user is not None:
            reply = str(final.text).strip() if final is not None else ""
            at = stamps.get(final.id, default) if final is not None and final.id else default
            out.append(Turn(at, str(user.text).strip(), reply))

    for message in messages:
        if isinstance(message, HumanMessage):
            close()
            user, final = message, None
        elif (
            isinstance(message, AIMessage) and not message.tool_calls and str(message.text).strip()
        ):
            final = message
    close()
    return out


def _render_turn(turn: Turn, zone: Any) -> str:
    reply = turn.reply or "(no reply)"
    return f"[{turn.at.astimezone(zone):%Y-%m-%d %H:%M}] User: {turn.user}\nAssistant: {reply}"


def make_chat_tools(state: Any) -> list[BaseTool]:
    """The chat tools, bound to the app's state (thread, turn-stats, routine and settings stores)."""

    async def _messages(record: ThreadRecord) -> list[BaseMessage]:
        """The active branch, as `GET /api/threads/{id}/messages` reads it.

        A whole `configurable` of its own: the running turn's (its thread,
        checkpoint and namespace) must not leak into the read.
        """
        configurable = {"thread_id": record.id, "checkpoint_ns": ""}
        if record.active_checkpoint_id:
            configurable["checkpoint_id"] = record.active_checkpoint_id
        snapshot = await state.agent.aget_state({"configurable": configurable})
        return list(snapshot.values.get("messages", []))

    async def _stamps(thread_id: str) -> dict[str, datetime]:
        store = getattr(state, "turn_stats_store", None)
        if store is None:
            return {}
        return {s.final_message_id: s.started_at for s in await store.list_for_thread(thread_id)}

    @tool
    async def list_chats(
        config: RunnableConfig,
        since: str | None = None,
        before: str | None = None,
        query: str | None = None,
        include_routine_runs: bool = False,
        limit: int = 20,
    ) -> str:
        """The user's chats with activity in a time window, newest first.

        `since`/`before` are dates or times (2026-10-17, 2026-10-17T18:00) in the user's
        timezone; `query` matches chat titles. Leaves out this chat, and routine runs unless
        `include_routine_runs`. Returns each chat's id, title, start and last activity: pass
        the id to read_chat.
        """
        user_id = _user_id(config)
        if user_id is None:
            return _NO_USER
        timezone = await _user_timezone(state, user_id)
        zone = sched.zone(timezone)
        try:
            start = parse_time(since, timezone) if since else None
            end = parse_time(before, timezone) if before else None
        except ValueError as exc:
            return str(exc)
        current = (config.get("configurable") or {}).get("thread_id")
        records = [
            r
            for r in await state.thread_store.list_for_owner(user_id)
            if r.id != current
            and (include_routine_runs or r.routine_id is None)
            and (start is None or r.updated_at >= start)
            and (end is None or r.created_at < end)
            and (not query or query.casefold() in r.title.casefold())
        ]
        records.sort(key=lambda r: r.updated_at, reverse=True)
        shown = records[: max(1, min(MAX_LIST, limit))]
        if not shown:
            return "No chats match."
        names = {}
        if any(r.routine_id for r in shown):
            names = {r.id: r.name for r in await state.routine_store.list_for_owner(user_id)}
        lines = [f"{len(shown)} of {len(records)} chats, newest first (times in {timezone}):"]
        for r in shown:
            line = (
                f'- {r.id} "{r.title}": started {r.created_at.astimezone(zone):%Y-%m-%d %H:%M}, '
                f"last activity {r.updated_at.astimezone(zone):%Y-%m-%d %H:%M}"
            )
            if r.routine_id:
                line += f' (a run of the routine "{names.get(r.routine_id, "deleted")}")'
            lines.append(line)
        return "\n".join(lines)

    @tool
    async def read_chat(
        chat_id: str, config: RunnableConfig, since: str | None = None, offset: int = 0
    ) -> str:
        """What was said in one of the user's chats: each user message and the final reply.

        Tool calls and their results aren't included. Long chats come in pages: call again
        with the `offset` the page ends with. `since` (a date or time in the user's
        timezone) keeps only later turns.
        """
        user_id = _user_id(config)
        if user_id is None:
            return _NO_USER
        record = await state.thread_store.get(chat_id.strip(), user_id)
        if record is None:
            return _not_found(chat_id)
        timezone = await _user_timezone(state, user_id)
        zone = sched.zone(timezone)
        try:
            start = parse_time(since, timezone) if since else None
        except ValueError as exc:
            return str(exc)
        all_turns = turns(await _messages(record), await _stamps(record.id), record.created_at)
        if start is not None:
            all_turns = [t for t in all_turns if t.at >= start]
        header = (
            f'Chat "{record.title}" ({record.id}), started '
            f"{record.created_at.astimezone(zone):%Y-%m-%d %H:%M} ({timezone}), "
            f"{len(all_turns)} turns"
        )
        offset = max(0, offset)
        if offset >= len(all_turns):
            return f"{header}. No turns from offset {offset}."
        parts: list[str] = []
        size = 0
        index = offset
        while index < len(all_turns):
            text = _render_turn(all_turns[index], zone)
            if parts and size + len(text) > PAGE_CHARS:
                break
            if len(text) > PAGE_CHARS:
                text = text[:PAGE_CHARS] + " [cut: this turn is longer than a page]"
            parts.append(text)
            size += len(text) + 2
            index += 1
        if index < len(all_turns):
            footer = f"(Turns {offset}-{index - 1}. More: read_chat with offset={index}.)"
        else:
            footer = "(End of chat.)"
        return f"{header}:\n\n" + "\n\n".join(parts) + f"\n\n{footer}"

    return [list_chats, read_chat]

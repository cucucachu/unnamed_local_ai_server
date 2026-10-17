"""`list_chats` / `read_chat` (`app/agent/chat_tools.py`, M21-05).

Most tests call the tools directly over in-memory stores and a stub
agent (`aget_state`); the last runs two real chat turns, so the second reads the
first's real checkpoint.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from app.agent.chat_tools import PAGE_CHARS, make_chat_tools
from app.db.settings import InMemorySettingsStore
from app.db.threads import InMemoryThreadStore
from app.db.turn_stats import InMemoryTurnStatsStore, TurnStat
from tests.fake_model.scripting import FakeModel, TextTurn, ToolCallTurn
from tests.fake_platform.scripting import FakePlatform
from tests.test_chat_ws import _assert_turn_end, _drain_turn, _make_client

ALICE, BOB = "alice-id", "bob-id"
T0 = datetime(2026, 10, 15, 9, 0, tzinfo=UTC)


class StubAgent:
    def __init__(self) -> None:
        # (thread id, checkpoint id or None for the latest) -> messages
        self.saved: dict[tuple[str, str | None], list] = {}

    async def aget_state(self, config):
        c = config["configurable"]
        messages = self.saved.get((c["thread_id"], c.get("checkpoint_id")))
        return SimpleNamespace(values={} if messages is None else {"messages": messages})


class StubRoutines:
    async def list_for_owner(self, owner_user_id: str):
        return [SimpleNamespace(id="r-1", name="Morning briefing")]


def make_state():
    return SimpleNamespace(
        thread_store=InMemoryThreadStore(),
        turn_stats_store=InMemoryTurnStatsStore(),
        routine_store=StubRoutines(),
        settings_store=InMemorySettingsStore(),
        agent=StubAgent(),
    )


def add_chat(state, chat_id, owner, title, *, days=0, routine_id=None, messages=None):
    record = state.thread_store.insert(chat_id, owner, title, routine_id)
    at = T0 + timedelta(days=days)
    state.thread_store._rows[chat_id] = replace(record, created_at=at, updated_at=at)
    if messages is not None:
        state.agent.saved[(chat_id, None)] = messages


async def call(state, name: str, user: str = ALICE, current: str = "current", **args) -> str:
    tools = {t.name: t for t in make_chat_tools(state)}
    config = {"configurable": {"user_id": user, "thread_id": current}}
    return await tools[name].ainvoke(args, config=config)


def exchange(user: str, reply: str, reply_id: str | None = None) -> list:
    return [HumanMessage(user), AIMessage(reply, id=reply_id)]


async def test_each_user_sees_only_their_own_chats():
    state = make_state()
    add_chat(state, "a1", ALICE, "Alice's trip", messages=exchange("hi", "hello"))
    add_chat(state, "b1", BOB, "Bob's secret", messages=exchange("psst", "ok"))

    listed = await call(state, "list_chats")
    assert "a1" in listed and "Alice's trip" in listed
    assert "b1" not in listed and "secret" not in listed
    assert await call(state, "read_chat", chat_id="b1") == (
        "Error: chat b1 not found. Call list_chats for the ids."
    )
    assert "psst" in await call(state, "read_chat", user=BOB, chat_id="b1")


async def test_the_current_chat_and_routine_runs_are_left_out_by_default():
    state = make_state()
    add_chat(state, "current", ALICE, "This chat")
    add_chat(state, "old", ALICE, "An old chat")
    add_chat(state, "run", ALICE, "Briefing run", routine_id="r-1")

    listed = await call(state, "list_chats")
    assert "old" in listed and "This chat" not in listed and "Briefing run" not in listed

    listed = await call(state, "list_chats", include_routine_runs=True)
    assert 'Briefing run": started' in listed
    assert 'a run of the routine "Morning briefing"' in listed


async def test_since_before_and_query_filter():
    state = make_state()
    for day, title in enumerate(["Garden plans", "Tax questions", "Garden pests"]):
        add_chat(state, f"c{day}", ALICE, title, days=day)

    listed = await call(state, "list_chats", since="2026-10-16")
    assert "Tax questions" in listed and "Garden pests" in listed and "Garden plans" not in listed
    listed = await call(state, "list_chats", before="2026-10-16T00:00")
    assert "Garden plans" in listed and "Tax questions" not in listed
    listed = await call(state, "list_chats", query="garden")
    assert listed.index("Garden pests") < listed.index("Garden plans")
    assert "Tax" not in listed
    assert (await call(state, "list_chats", since="last week")).startswith("Error: 'last week'")
    assert await call(state, "list_chats", query="nothing like it") == "No chats match."


async def test_a_chat_reads_as_user_messages_and_final_replies_only():
    state = make_state()
    messages = [
        HumanMessage("What's in my notes about the trip?"),
        AIMessage(
            "Let me look.",
            tool_calls=[{"id": "c1", "name": "read_file", "args": {"file_path": "/personal/t.md"}}],
        ),
        ToolMessage("SECRET TOOL RESULT", tool_call_id="c1", name="read_file"),
        AIMessage(
            [
                {"type": "reasoning", "reasoning": "PRIVATE THINKING"},
                {"type": "text", "text": "You're flying to Lisbon on the 3rd."},
            ],
            id="final-1",
        ),
        HumanMessage("Thanks!"),
        AIMessage("Any time.", id="final-2"),
    ]
    add_chat(state, "trip", ALICE, "Trip", messages=messages)
    await state.turn_stats_store.upsert(
        TurnStat("trip", "final-2", "completed", 1200, T0 + timedelta(hours=2))
    )

    out = await call(state, "read_chat", chat_id="trip")
    for hidden in ("SECRET TOOL RESULT", "PRIVATE THINKING", "Let me look", "read_file"):
        assert hidden not in out
    assert out == (
        'Chat "Trip" (trip), started 2026-10-15 09:00 (UTC), 2 turns:\n\n'
        "[2026-10-15 09:00] User: What's in my notes about the trip?\n"
        "Assistant: You're flying to Lisbon on the 3rd.\n\n"
        "[2026-10-15 11:00] User: Thanks!\n"
        "Assistant: Any time.\n\n"
        "(End of chat.)"
    )

    later = await call(state, "read_chat", chat_id="trip", since="2026-10-15T10:00")
    assert "Thanks!" in later and "Lisbon" not in later


async def test_a_turn_without_a_reply_says_so():
    state = make_state()
    messages = [
        HumanMessage("Delete everything"),
        AIMessage("", tool_calls=[{"id": "c1", "name": "delete", "args": {"path": "/x"}}]),
    ]
    add_chat(state, "pending", ALICE, "Pending", messages=messages)
    assert "Assistant: (no reply)" in await call(state, "read_chat", chat_id="pending")


async def test_long_chats_page_and_the_pages_round_trip():
    state = make_state()
    messages = []
    for n in range(10):
        messages += exchange(f"question {n} " + "q" * 3000, f"answer {n}")
    add_chat(state, "long", ALICE, "Long", messages=messages)

    seen, offset, pages = [], 0, 0
    while True:
        page = await call(state, "read_chat", chat_id="long", offset=offset)
        pages += 1
        body = page.split(":\n\n", 1)[1]
        assert len(body) <= PAGE_CHARS + 200
        seen += [n for n in range(10) if f"User: question {n} " in page]
        if page.endswith("(End of chat.)"):
            break
        offset = int(page.rsplit("offset=", 1)[1].rstrip(".)"))
    assert seen == list(range(10)) and pages > 1
    assert "No turns from offset 10" in await call(state, "read_chat", chat_id="long", offset=10)


async def test_an_edited_chat_reads_its_active_branch():
    state = make_state()
    add_chat(state, "edited", ALICE, "Edited", messages=exchange("first try", "abandoned"))
    state.agent.saved[("edited", "cp-branch")] = exchange("second try", "kept")
    record = state.thread_store._rows["edited"]
    state.thread_store._rows["edited"] = replace(record, active_checkpoint_id="cp-branch")

    out = await call(state, "read_chat", chat_id="edited")
    assert "second try" in out and "kept" in out
    assert "first try" not in out and "abandoned" not in out


async def test_read_chat_over_a_real_checkpoint(fake_model: FakeModel, fake_platform: FakePlatform):
    fake_platform.personal()["notes.md"] = b"SECRET FILE BODY"
    fake_model.queue(
        ToolCallTurn(name="read_file", args={"file_path": "/personal/notes.md"}),
        TextTurn("Your notes say hello."),
    )
    with _make_client(fake_model, fake_platform) as client:
        with client.websocket_connect("/ws/chat/chat-a") as ws:
            ws.send_json({"type": "user_message", "content": "what do my notes say?"})
            _assert_turn_end(_drain_turn(ws)[-1], "completed")

        fake_model.queue(
            ToolCallTurn(name="read_chat", args={"chat_id": "chat-a"}),
            ToolCallTurn(name="list_chats", args={}),
            TextTurn("done"),
        )
        with client.websocket_connect("/ws/chat/chat-b") as ws:
            ws.send_json({"type": "user_message", "content": "what did we talk about?"})
            frames = _drain_turn(ws)

    _assert_turn_end(frames[-1], "completed")
    assert not any(f["type"] == "approval_request" for f in frames)
    told = [m["content"] for m in fake_model.requests[-1]["messages"] if m.get("role") == "tool"]
    read, listed = told[-2], told[-1]
    assert "User: what do my notes say?" in read
    assert "Assistant: Your notes say hello." in read
    assert "SECRET FILE BODY" not in read and "read_file" not in read
    assert "chat-a" in listed and "chat-b" not in listed

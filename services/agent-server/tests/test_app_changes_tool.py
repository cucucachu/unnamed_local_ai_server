"""`list_app_changes` (`app/agent/app_changes_tool.py`, M22-12) through a real chat turn.

The fake platform answers `GET /apps/changes` from scripted rows, limited to the
caller's spaces; which rows a routine run sees is the platform's rule (its tests).
"""

from __future__ import annotations

from app.agent.approvals import DEFAULT_POLICY
from app.agent.tool_catalog import NAMES
from app.db.settings import InMemorySettingsStore
from app.routines.delivered import MEMORY
from tests.fake_identity import TEST_USER_ID
from tests.fake_model.scripting import FakeModel, TextTurn, ToolCallTurn
from tests.fake_platform.scripting import FakePlatform
from tests.test_chat_ws import _assert_turn_end, _drain_turn, _make_client

BOB = "22222222-2222-4222-8222-222222222222"


def _row(space_key: str, row_id: int, at: str, title: str, text: str, by: str, **kw) -> dict:
    return {
        "space_key": space_key,
        "app": kw.pop("app", "Notes"),
        "table": "notes",
        "row_id": row_id,
        "title": title,
        "text": f"{title}\n{text}",
        "updated_at": at,
        "created_by": kw.pop("created_by", by),
        "updated_by": by,
    }


def _run(fake_model: FakeModel, fake_platform: FakePlatform, *calls: dict, **client) -> list[dict]:
    fake_model.queue(
        *(ToolCallTurn(name="list_app_changes", args=args) for args in calls), TextTurn("done")
    )
    with (
        _make_client(fake_model, fake_platform, **client) as c,
        c.websocket_connect("/ws/chat/changes") as ws,
    ):
        ws.send_json({"type": "user_message", "content": "what changed in my apps?"})
        return _drain_turn(ws)


def _told(fake_model: FakeModel) -> list[str]:
    return [m["content"] for m in fake_model.requests[-1]["messages"] if m.get("role") == "tool"]


async def _in_lisbon() -> InMemorySettingsStore:
    store = InMemorySettingsStore()
    await store.update_document(TEST_USER_ID, {"timezone": "Europe/Lisbon"})
    return store


async def test_rows_come_with_who_wrote_them_from_the_users_spaces(fake_model, fake_platform):
    fake_platform.add_space("family", {TEST_USER_ID: "editor", BOB: "editor"})
    fake_platform.add_space("work", {BOB: "owner"})
    fake_platform.user_names[BOB] = "Bob"
    personal = f"personal:{TEST_USER_ID}"
    fake_platform.app_changes = [
        _row("family", 2, "2026-10-17T18:30:00.000Z", "Rota", "Bob does Fridays.", BOB),
        _row(personal, 1, "2026-10-17T09:15:00.000Z", "Allergy", "penicillin", TEST_USER_ID),
        _row("family", 3, "2026-10-17T19:00:00.000Z", "Pick-up", "kids at 4", TEST_USER_ID,
             created_by=BOB),
        _row("work", 1, "2026-10-17T10:00:00.000Z", "Bob's secret", "not hers", BOB),
        _row(personal, 4, "2026-10-15T08:00:00.000Z", "Old", "before since", TEST_USER_ID),
    ]  # fmt: skip
    frames = _run(
        fake_model,
        fake_platform,
        {"since": "2026-10-17"},
        settings_store=await _in_lisbon(),
    )

    _assert_turn_end(frames[-1], "completed")
    assert not any(f["type"] == "approval_request" for f in frames)
    start = next(f for f in frames if f["type"] == "tool_start")
    assert (start["name"], start["category"]) == ("list_app_changes", "app")
    [(bearer, params)] = fake_platform.app_changes_requests
    assert fake_platform.principal(bearer) is not None
    # Midnight in Lisbon (UTC+1 in October), sent as UTC.
    assert params == {"since": "2026-10-16T23:00:00+00:00", "offset": "0", "limit": "50"}
    assert _told(fake_model) == [
        (
            "3 app rows changed since 2026-10-17 00:00 (times in Europe/Lisbon):\n\n"
            "[2026-10-17 10:15] Notes (Personal) notes #1, by you: Allergy\n  penicillin\n\n"
            "[2026-10-17 19:30] Notes (Family) notes #2, by Bob: Rota\n  Bob does Fridays.\n\n"
            "[2026-10-17 20:00] Notes (Family) notes #3, added by Bob, last changed by you: "
            "Pick-up\n  kids at 4\n\n"
            "(End of changes.)"
        )
    ]


async def test_long_lists_come_in_pages(fake_model, fake_platform):
    personal = f"personal:{TEST_USER_ID}"
    fake_platform.app_changes = [
        _row(personal, i, f"2026-10-17T10:{i:02d}:00.000Z", f"Note {i}", "x" * 1500, TEST_USER_ID)
        for i in range(12)
    ]
    _run(
        fake_model,
        fake_platform,
        {"since": "2026-10-17T00:00"},
        {"since": "2026-10-17T00:00", "offset": 7},
    )
    first, second = _told(fake_model)
    assert first.startswith("12 app rows changed since 2026-10-17 00:00 (times in UTC):")
    assert first.count("by you: Note") == 7
    assert first.endswith("(Rows 0-6. More: list_app_changes with offset=7.)")
    assert "by you: Note 7\n" in second and second.count("by you: Note") == 5
    assert second.endswith("(End of changes.)")
    assert [p["offset"] for _, p in fake_platform.app_changes_requests] == ["0", "7"]


async def test_nothing_changed_and_a_bad_time_are_said_plainly(fake_model, fake_platform):
    _run(fake_model, fake_platform, {"since": "2026-10-17"}, {"since": "last tuesday"})
    nothing, bad = _told(fake_model)
    assert nothing == "No app rows changed since 2026-10-17 00:00 (UTC)."
    assert bad.startswith("Error: 'last tuesday' isn't a date or time")
    assert len(fake_platform.app_changes_requests) == 1


async def test_it_is_read_only_and_the_memory_routine_keeps_it():
    assert "list_app_changes" in NAMES and "list_app_changes" in DEFAULT_POLICY.allowed
    assert "list_app_changes" not in MEMORY.unavailable_tools

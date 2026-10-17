"""The Memory routine (M21-10, docs/MEMORY.md §4.6): the real definition."""

# ruff: noqa: F811 - the scheduler tests' fixtures, imported

from __future__ import annotations

import re
from datetime import datetime, timedelta

import pytest

from app.agent.approvals import ToolPolicy
from app.routines import delivered, memory_prompt
from app.routines.runs import RUN_MARKER
from tests.fake_model.scripting import FakeModel, TextTurn
from tests.test_delivered_routines import OFF, _first_message, _run_scheduled, _set_timezone
from tests.test_hitl_approvals import _bound_tools
from tests.test_routine_scheduler import (  # noqa: F401
    ALICE,
    HEADERS,
    NOW,
    _fixed_now,
    _routine,
    _runs,
    client,
    harness,
    tool_policies,
)


@pytest.fixture(autouse=True)
def _now(monkeypatch) -> None:
    monkeypatch.setattr(delivered, "_now", lambda: NOW)


def _memory(client) -> dict:
    _set_timezone(client)
    (routine,) = client.get("/api/routines", headers=HEADERS).json()
    return routine


def test_every_user_gets_it_quiet_at_night_without_the_web_or_integrations(client) -> None:
    routine = _memory(client)
    offset = delivered.offset_minutes(ALICE)
    assert routine["name"] == "Memory" and routine["delivered"]
    assert routine["schedule"]["time"].startswith(f"{3 + offset // 60:02d}:{offset % 60:02d}")
    assert routine["prompt"] == memory_prompt.DEFAULT and routine["prompt_is_default"]
    assert {t["name"] for t in routine["unavailable_tools"]} == OFF
    assert delivered.MEMORY.quiet
    assert set(delivered.DEFINITIONS) == {"memory"}


def test_the_prompt_fills_in_only_since_and_now() -> None:
    filled = memory_prompt.FIXED.format(since="S", now="N")
    assert "from S to now (N)" in filled and 'since="S"' in filled
    assert "{" not in filled.replace("{since}", "").replace("{now}", "")


def test_a_run_reads_chats_and_files_but_not_the_web(
    client, harness, fake_model: FakeModel, tool_policies
) -> None:
    tool_policies.policy = ToolPolicy.allow_all()
    routine = _memory(client)
    fake_model.queue(TextTurn("Nothing new to remember."))
    run = _run_scheduled(client, harness, routine)
    assert run["status"] == "succeeded"

    message = _first_message(client, run["thread_id"])
    assert message.startswith(f'{RUN_MARKER} "Memory" (space /personal)')
    window = re.search(r"Cover their chats from (\S+) to now \((\S+)\)\.", message)
    assert window, message
    since, now = map(datetime.fromisoformat, window.groups())
    assert now - since == timedelta(days=14)
    assert f'list_chats with since="{window[1]}"' in message
    assert "Facts about the user come only from the user's own messages." in message
    assert message.endswith(memory_prompt.DEFAULT)

    tools = _bound_tools(fake_model.requests[0])
    assert {"list_chats", "read_chat", "search_files", "read_file", "ls", "grep"} <= tools
    assert {"write_file", "edit_file", "delete", "execute_code"} <= tools
    assert OFF.isdisjoint(tools)

"""`search_files` (`app/agent/search_tool.py`, M21-04) through a real chat turn.

The fake platform (`tests/fake_platform`) answers `POST /files/search` with
scripted results; the ranking itself is the platform's (its own tests).
"""

from __future__ import annotations

from app.agent.approvals import DEFAULT_POLICY, ToolPolicy
from app.agent.tool_catalog import NAMES
from tests.fake_model.scripting import FakeModel, TextTurn, ToolCallTurn
from tests.fake_platform.scripting import FakePlatform
from tests.fake_tool_policies import FakeToolPolicies
from tests.test_chat_ws import _assert_turn_end, _drain_turn, _make_client
from tests.test_hitl_approvals import _bound_tools

HIT = {
    "path": "/personal/Memory/people/maria.md",
    "line_start": 3,
    "line_end": 5,
    "snippet": "Allergic to peanuts.\nCarries an EpiPen.",
    "score": 0.032,
}


def _run(fake_model: FakeModel, fake_platform: FakePlatform, args: dict, **client) -> list[dict]:
    fake_model.queue(ToolCallTurn(name="search_files", args=args), TextTurn("done"))
    with (
        _make_client(fake_model, fake_platform, **client) as c,
        c.websocket_connect("/ws/chat/search") as ws,
    ):
        ws.send_json({"type": "user_message", "content": "what can't Maria eat?"})
        return _drain_turn(ws)


def _told(fake_model: FakeModel) -> str:
    return [m for m in fake_model.requests[-1]["messages"] if m.get("role") == "tool"][-1][
        "content"
    ]


async def test_search_sends_the_delegation_and_renders_hits(fake_model, fake_platform):
    fake_platform.search_results = [HIT]
    frames = _run(fake_model, fake_platform, {"query": "food allergies", "path": "personal"})

    _assert_turn_end(frames[-1], "completed")
    assert not any(f["type"] == "approval_request" for f in frames)
    start = next(f for f in frames if f["type"] == "tool_start")
    assert (start["name"], start["category"]) == ("search_files", "file")
    [(bearer, body)] = fake_platform.search_requests
    assert bearer is not None and fake_platform.principal(bearer) is not None
    assert body == {"query": "food allergies", "path": "/personal", "limit": 10}
    assert _told(fake_model) == (
        '1 match for "food allergies" under /personal, best first:\n'
        "\n/personal/Memory/people/maria.md (lines 3-5)\n"
        "  Allergic to peanuts.\n"
        "  Carries an EpiPen."
    )


def _row(space: dict, table: str = "recipes") -> dict:
    return {
        "kind": "app_row",
        "path": None,
        "line_start": None,
        "line_end": None,
        "app_row": {
            "instance_id": "11111111-1111-4111-8111-111111111111",
            "app": {"slug": "recipes", "name": "Recipes"},
            "space": space,
            "table": table,
            "row_id": 12,
            "title": "Peanut noodles",
        },
        "snippet": "Peanut noodles\ncuisine: Thai",
        "score": 0.031,
    }


async def test_app_rows_come_with_the_query_that_reads_them(fake_model, fake_platform):
    personal = {"id": "p", "slug": "alice", "name": "Alice", "kind": "personal"}
    family = {"id": "f", "slug": "family", "name": "Family", "kind": "shared"}
    fake_platform.search_results = [_row(personal), HIT, _row(family, "my recipes")]
    _run(fake_model, fake_platform, {"query": "peanuts"})
    assert _told(fake_model) == (
        '3 matches for "peanuts" under /, best first:\n'
        "\nRecipes (Personal) recipes #12: Peanut noodles\n"
        '  (app row: app_query(instance="personal/recipes", '
        'sql="SELECT * FROM recipes WHERE rowid = 12"))\n'
        "  Peanut noodles\n"
        "  cuisine: Thai\n"
        "\n/personal/Memory/people/maria.md (lines 3-5)\n"
        "  Allergic to peanuts.\n"
        "  Carries an EpiPen.\n"
        "\nRecipes (Family) my recipes #12: Peanut noodles\n"
        '  (app row: app_query(instance="family/recipes", '
        'sql="SELECT * FROM [my recipes] WHERE rowid = 12"))\n'
        "  Peanut noodles\n"
        "  cuisine: Thai"
    )


async def test_words_only_and_no_results_are_said_plainly(fake_model, fake_platform):
    fake_platform.search_semantic = False
    _run(fake_model, fake_platform, {"query": "boat name", "limit": 500})

    [(_, body)] = fake_platform.search_requests
    assert body["limit"] == 50 and body["path"] == "/"
    told = _told(fake_model)
    assert told.startswith('No matches for "boat name" under /.')
    assert "Matched by words only" in told


async def test_a_platform_error_is_a_tool_result_not_a_failed_turn(fake_model, fake_platform):
    fake_platform.search_error = (404, "not_found")
    frames = _run(fake_model, fake_platform, {"query": "x", "path": "/spaces/nope"})
    _assert_turn_end(frames[-1], "completed")
    assert _told(fake_model).startswith("Error: /spaces/nope not found")

    fake_platform.search_error = (503, "unavailable")
    frames = _run(fake_model, fake_platform, {"query": "x"})
    _assert_turn_end(frames[-1], "completed")
    assert _told(fake_model) == "Error: file search failed (HTTP 503). Try grep instead."


async def test_it_is_in_the_catalog_and_asks_when_set_to_ask(fake_model, fake_platform):
    assert "search_files" in NAMES and "search_files" in DEFAULT_POLICY.allowed
    policies = FakeToolPolicies(ToolPolicy(NAMES, frozenset()))
    frames = _run(fake_model, fake_platform, {"query": "maria"}, tool_policies=policies)
    approval = next(f for f in frames if f["type"] == "approval_request")
    assert approval["actions"][0]["description"] == "Search files: maria"
    assert fake_platform.search_requests == []


async def test_turned_off_it_is_not_bound_and_is_refused(fake_model, fake_platform):
    policies = FakeToolPolicies(ToolPolicy(NAMES - {"search_files"}, DEFAULT_POLICY.allowed))
    frames = _run(fake_model, fake_platform, {"query": "maria"}, tool_policies=policies)

    _assert_turn_end(frames[-1], "completed")
    assert "search_files" not in _bound_tools(fake_model.requests[0])
    assert "turned off" in _told(fake_model)
    assert fake_platform.search_requests == []

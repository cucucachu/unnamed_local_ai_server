"""The repeated-call guard and the step limit (#383), unit and through a chat turn."""

from __future__ import annotations

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from app.agent.approvals import ToolPolicy
from app.agent.repeat_guard import ALLOWED, STOP, RepeatedToolCall, RepeatGuardMiddleware
from tests.fake_model.scripting import FakeModel, TextTurn, ToolCallTurn
from tests.fake_platform.scripting import FakePlatform
from tests.test_chat_ws import _drain_turn, _make_client

TODOS = {"todos": [{"content": "look", "status": "pending"}]}


def _call(n: int, args: dict | None = None, name: str = "write_todos") -> AIMessage:
    return AIMessage(content="", tool_calls=[{"name": name, "args": args or TODOS, "id": f"c{n}"}])


class _Request:
    def __init__(self, messages: list) -> None:
        self.state = {"messages": messages}
        self.tool_call = messages[-1].tool_calls[0]


def _ran(_request) -> ToolMessage:
    return ToolMessage(content="ran", tool_call_id="x")


def _history(calls: int, earlier: int = 0) -> list:
    messages: list = [HumanMessage("before")]
    messages += [_call(100 + n) for n in range(earlier)]
    messages.append(HumanMessage("now"))
    messages += [_call(n) for n in range(calls)]
    return messages


def test_repeats_up_to_the_allowance_run() -> None:
    guard = RepeatGuardMiddleware()
    assert guard.wrap_tool_call(_Request(_history(ALLOWED)), _ran).content == "ran"


def test_one_more_is_refused_without_running() -> None:
    result = RepeatGuardMiddleware().wrap_tool_call(_Request(_history(ALLOWED + 1)), _ran)
    assert result.status == "error"
    assert result.content.startswith("Not run: you already called write_todos")
    assert f"{ALLOWED} times in this turn" in result.content


def test_only_this_turn_and_the_same_arguments_count() -> None:
    guard = RepeatGuardMiddleware()
    assert guard.wrap_tool_call(_Request(_history(1, earlier=10)), _ran).content == "ran"
    other = _history(ALLOWED + 1) + [_call(99, {"todos": []})]
    assert guard.wrap_tool_call(_Request(other), _ran).content == "ran"


def test_the_stop_ends_the_turn() -> None:
    guard = RepeatGuardMiddleware()
    assert guard.after_model({"messages": _history(STOP - 1)}, None) is None
    with pytest.raises(RepeatedToolCall, match=r"repeating the same step \(write_todos\)"):
        guard.after_model({"messages": _history(STOP)}, None)


def _tool_result(request: dict) -> str:
    message = request["messages"][-1]
    assert message["role"] == "tool", message
    content = message["content"]
    return content if isinstance(content, str) else "".join(p.get("text", "") for p in content)


async def test_a_looping_model_is_told_then_stopped(
    fake_model: FakeModel, fake_platform: FakePlatform
) -> None:
    fake_model.queue(*[ToolCallTurn("write_todos", TODOS) for _ in range(STOP)])
    with (
        _make_client(fake_model, fake_platform, tool_policy=ToolPolicy.allow_all()) as client,
        client.websocket_connect("/ws/chat/repeat-guard") as ws,
    ):
        ws.send_json({"type": "user_message", "content": "plan"})
        frames = _drain_turn(ws)
    assert frames[-1]["type"] == "error"
    assert "kept repeating the same step (write_todos)" in frames[-1]["message"]
    assert len(fake_model.requests) == STOP
    results = [_tool_result(r) for r in fake_model.requests[1:]]
    assert not any(r.startswith("Not run") for r in results[:ALLOWED])
    assert all(r.startswith("Not run") for r in results[ALLOWED:])


async def test_the_step_limit_says_so_in_plain_words(
    fake_model: FakeModel, fake_platform: FakePlatform
) -> None:
    fake_model.queue(
        *[
            ToolCallTurn("write_todos", {"todos": [{"content": str(n), "status": "pending"}]})
            for n in range(10)
        ],
        TextTurn("done"),
    )
    with (
        _make_client(
            fake_model, fake_platform, tool_policy=ToolPolicy.allow_all(), agent_recursion_limit=8
        ) as client,
        client.websocket_connect("/ws/chat/step-limit") as ws,
    ):
        ws.send_json({"type": "user_message", "content": "plan"})
        frames = _drain_turn(ws)
    assert frames[-1] == {
        "type": "error",
        "message": "The agent took too many steps without finishing, so it stopped. Try "
        "rephrasing the request, or breaking it into smaller steps.",
    }

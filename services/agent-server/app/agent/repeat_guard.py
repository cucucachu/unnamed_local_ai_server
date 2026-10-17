"""Stops a turn that keeps repeating the same tool call (#383).

A model can get stuck calling one tool with the same arguments over and
over until the recursion limit ends the turn minutes later (#301; Ornith on
`open_integration` in a routine run). Counted within the turn: since the
last user message. Past `ALLOWED` identical calls the call isn't run and
the model is told it already has the answer; at `STOP` the turn ends with
`RepeatedToolCall`, which the turn runner shows like any stopped turn (in
chat, and as a routine run's error).
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from typing import Any

from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware.types import ToolCallRequest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

ALLOWED = 3
STOP = 6


def _key(name: str, args: Any) -> str:
    return f"{name}\0{json.dumps(args, sort_keys=True, default=str)}"


def _this_turn(messages: list[Any]) -> list[Any]:
    for index in range(len(messages) - 1, -1, -1):
        if isinstance(messages[index], HumanMessage):
            return messages[index + 1 :]
    return messages


def _counts(messages: list[Any]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for message in _this_turn(messages):
        if isinstance(message, AIMessage):
            for call in message.tool_calls:
                key = _key(call["name"], call.get("args"))
                counts[key] = counts.get(key, 0) + 1
    return counts


def _messages(state: Any) -> list[Any]:
    messages = state.get("messages") if isinstance(state, dict) else None
    return messages if isinstance(messages, list) else []


class RepeatedToolCall(RuntimeError):
    def __init__(self, tool: str) -> None:
        super().__init__(
            f"The agent kept repeating the same step ({tool}) without getting anywhere, so it "
            "stopped. Try rephrasing the request, or breaking it into smaller steps."
        )
        self.tool = tool


class RepeatGuardMiddleware(AgentMiddleware):
    def _refusal(self, request: ToolCallRequest) -> ToolMessage | None:
        call = request.tool_call
        count = _counts(_messages(request.state)).get(_key(call["name"], call.get("args")), 0)
        if count <= ALLOWED:
            return None
        return ToolMessage(
            content=(
                f"Not run: you already called {call['name']} with exactly these arguments "
                f"{count - 1} times in this turn, and the results are above. Don't call it again "
                "with the same arguments: use what you have, try something different, or answer "
                "the user."
            ),
            tool_call_id=call["id"],
            name=call["name"],
            status="error",
        )

    def wrap_tool_call(
        self, request: ToolCallRequest, handler: Callable[[ToolCallRequest], Any]
    ) -> Any:
        return self._refusal(request) or handler(request)

    async def awrap_tool_call(
        self, request: ToolCallRequest, handler: Callable[[ToolCallRequest], Awaitable[Any]]
    ) -> Any:
        return self._refusal(request) or await handler(request)

    def after_model(self, state: Any, runtime: Any) -> dict[str, Any] | None:
        messages = _messages(state)
        last = messages[-1] if messages else None
        if not isinstance(last, AIMessage) or not last.tool_calls:
            return None
        counts = _counts(messages)
        for call in last.tool_calls:
            if counts.get(_key(call["name"], call.get("args")), 0) >= STOP:
                raise RepeatedToolCall(call["name"])
        return None

    async def aafter_model(self, state: Any, runtime: Any) -> dict[str, Any] | None:
        return self.after_model(state, runtime)

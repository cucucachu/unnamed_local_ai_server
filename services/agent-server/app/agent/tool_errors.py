"""Shortens tool-argument validation errors before the model reads them.

LangGraph's `ToolInvocationError` text repeats every argument the model sent
(`Error invoking tool 'edit_file' with kwargs {...}`). For a file edit that
is the whole `new_string` again, kept in the history for the rest of the
thread. The model already has those arguments in its own tool call, so the
error keeps only the tool name and what was wrong.
"""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from typing import Any

from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware.types import ToolCallRequest
from langchain_core.messages import ToolMessage

_INVOCATION_ERROR = re.compile(
    r"\AError invoking tool '(?P<name>[^']+)' with kwargs .*? with error:\n(?P<errors>.*?)"
    r"\n Please fix the error and try again\.\Z",
    re.DOTALL,
)


def compact_invocation_error(content: str) -> str | None:
    match = _INVOCATION_ERROR.match(content)
    if match is None:
        return None
    errors = match["errors"].strip()
    return f"Error: invalid arguments for {match['name']}:\n{errors}\nCall it again with valid arguments."


def _compact(result: Any) -> Any:
    if not isinstance(result, ToolMessage) or result.status != "error":
        return result
    if not isinstance(result.content, str):
        return result
    short = compact_invocation_error(result.content)
    if short is None:
        return result
    return result.model_copy(update={"content": short})


class CompactToolErrorsMiddleware(AgentMiddleware):
    def wrap_tool_call(
        self, request: ToolCallRequest, handler: Callable[[ToolCallRequest], Any]
    ) -> Any:
        return _compact(handler(request))

    async def awrap_tool_call(
        self, request: ToolCallRequest, handler: Callable[[ToolCallRequest], Awaitable[Any]]
    ) -> Any:
        return _compact(await handler(request))

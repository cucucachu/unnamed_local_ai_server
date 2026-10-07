"""Extra lines on a file tool's result, past what deepagents' fixed wording carries.

deepagents formats `edit_file`'s success as one fixed sentence from the
backend's `EditResult`, so anything else the model should know about the
edit (that it only matched ignoring whitespace) is collected here while the
tool runs and appended to its ToolMessage afterwards.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from contextvars import ContextVar
from typing import Any

from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware.types import ToolCallRequest
from langchain_core.messages import ToolMessage

_notes: ContextVar[list[str] | None] = ContextVar("tool_notes", default=None)


def add_note(text: str) -> None:
    """Appended to the result of the tool call running now (no-op outside one)."""
    notes = _notes.get()
    if notes is not None:
        notes.append(text)


def _with_notes(result: Any, notes: list[str]) -> Any:
    if not notes or not isinstance(result, ToolMessage) or result.status == "error":
        return result
    if not isinstance(result.content, str):
        return result
    return result.model_copy(update={"content": "\n".join([result.content, *notes])})


class ToolNotesMiddleware(AgentMiddleware):
    def wrap_tool_call(
        self, request: ToolCallRequest, handler: Callable[[ToolCallRequest], Any]
    ) -> Any:
        notes: list[str] = []
        token = _notes.set(notes)
        try:
            return _with_notes(handler(request), notes)
        finally:
            _notes.reset(token)

    async def awrap_tool_call(
        self, request: ToolCallRequest, handler: Callable[[ToolCallRequest], Awaitable[Any]]
    ) -> Any:
        notes: list[str] = []
        token = _notes.set(notes)
        try:
            return _with_notes(await handler(request), notes)
        finally:
            _notes.reset(token)

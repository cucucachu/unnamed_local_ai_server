"""Who decides on a call that changes something: the chat's HITL setting, or a routine's mode.

A chat follows the user's `hitl_enabled` setting (M8-03). A routine run
(M17-05) sets `configurable["approval_mode"]` instead, from the routine:

- `ask` (the default): every call that a chat with HITL on would stop for
  stops, whatever the user's chat setting; the run waits in its thread
  (`waiting_approval`) for the owner to decide there.
- `allow_writes`: file writes and edits, code, app data writes and actions
  go ahead (a scheduled run's grant reaches only the routine's space). A
  delete or a destructive migration still stops for approval.
- `read_only`: all of those are refused with a tool error the model reads
  (`ReadOnlyRunMiddleware`, and `app_sql` for a statement that writes);
  reads still work.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, get_args

from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware.types import ToolCallRequest
from langchain_core.messages import ToolMessage
from langchain_core.runnables import RunnableConfig

from app.db.routines import ApprovalMode

APPROVAL_MODES: tuple[ApprovalMode, ...] = get_args(ApprovalMode)

# Tools that only change things (`app_sql` decides per statement, in the tool).
WRITE_TOOLS = frozenset(
    {
        "write_file",
        "edit_file",
        "replace_symbol",
        "delete",
        "execute_code",
        "create_app",
        "build_app",
        "app_action",
        "approve_migration",
    }
)
# What still stops for approval under `allow_writes`.
DESTRUCTIVE_TOOLS = frozenset({"delete", "approve_migration"})


def read_only_error(what: str) -> str:
    return (
        f"Error: this routine is read-only, so it can't {what}. "
        "Don't retry; say in your reply what you would have changed."
    )


def _configurable(config: RunnableConfig | None) -> dict:
    return (config or {}).get("configurable") or {}


def approval_mode(config: RunnableConfig | None) -> ApprovalMode | None:
    mode = _configurable(config).get("approval_mode")
    return mode if mode in APPROVAL_MODES else None


def is_read_only(config: RunnableConfig | None) -> bool:
    return approval_mode(config) == "read_only"


def needs_approval(config: RunnableConfig | None, tool_name: str) -> bool:
    """Whether a call that a chat with HITL on would stop for stops in this run.

    HITL-on is the default when nothing says otherwise: it guards file
    writes and code execution, so that's the safer way to fail.
    """
    mode = approval_mode(config)
    if mode is None:
        return bool(_configurable(config).get("hitl_enabled", True))
    if mode == "allow_writes":
        return tool_name in DESTRUCTIVE_TOOLS
    return mode == "ask"


def _refusal(request: ToolCallRequest) -> ToolMessage | None:
    call = request.tool_call
    if call["name"] not in WRITE_TOOLS or not is_read_only(request.runtime.config):
        return None
    what = "run code" if call["name"] == "execute_code" else f"use {call['name']}"
    return ToolMessage(
        content=read_only_error(what), tool_call_id=call["id"], name=call["name"], status="error"
    )


class ReadOnlyRunMiddleware(AgentMiddleware):
    """Refuses `WRITE_TOOLS` in a `read_only` routine run, without running them."""

    def wrap_tool_call(
        self, request: ToolCallRequest, handler: Callable[[ToolCallRequest], Any]
    ) -> Any:
        return _refusal(request) or handler(request)

    async def awrap_tool_call(
        self, request: ToolCallRequest, handler: Callable[[ToolCallRequest], Awaitable[Any]]
    ) -> Any:
        return _refusal(request) or await handler(request)

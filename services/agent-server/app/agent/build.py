"""Assembles the deep agent: local model client + real-disk filesystem backend.

NOTE on `deepagents==0.7.11` vs. the ticket's illustrative snippet: introspection
of the installed package (`inspect.signature(deepagents.create_deep_agent)`)
confirmed `create_deep_agent` accepts `checkpointer` directly as a keyword
argument (typed `Checkpointer | None`, i.e. `None | bool | BaseCheckpointSaver`)
and returns an already-compiled `CompiledStateGraph` wired up with that
checkpointer — no separate `.compile(checkpointer=...)` call is needed, unlike
some other langgraph-based builders. `deepagents.backends.FilesystemBackend`
also matches the ticket's snippet exactly, including the `virtual_mode` flag
name (confirmed via `inspect.signature(FilesystemBackend.__init__)`), which is
the real path-traversal guard: with `virtual_mode=True`, all paths are treated
as virtual paths anchored to `root_dir`, `..`/`~` traversal is blocked, and
every resolved path is verified to stay within `root_dir`. This is mandatory
for a workspace-rooted agent and is not skipped or swapped for another mode.

## M8-03 `interrupt_on` (human-in-the-loop approvals)

**Finding (documented per the ticket's explicit request): the direct
`InterruptOnConfig.when` predicate mechanism WORKS as-is — no dual-compiled-
-graph fallback is needed.** Verified empirically with a throwaway pytest
probe (against the real `HumanInTheLoopMiddleware`/`ToolCallRequest`/
`ToolRuntime` from the installed `langchain==1.6.x`/`deepagents==0.7.11`,
deleted after confirming, per the ticket's "write a tiny throwaway test/
script first" instruction) before writing this code:

- `HumanInTheLoopMiddleware._should_interrupt` (see
  `langchain/agents/middleware/human_in_the_loop.py`) builds a `ToolRuntime`
  whose `.config` is populated from `langgraph.config.get_config()` — the
  REAL `RunnableConfig` for the in-flight run (not a stripped-down copy).
  `req.runtime.config["configurable"]` — including any extra key a caller
  put there, e.g. `configurable={"thread_id": ..., "hitl_enabled": True}` —
  is visible inside `when(req)` at call time. Confirmed with a live
  `agent.astream_events(..., config={"configurable": {"thread_id": ...,
  "hitl_enabled": True}})` call against the fake-model harness: the `when`
  predicate observed `hitl_enabled=True` in `req.runtime.config
  ["configurable"]` and correctly triggered a pending `Interrupt` (verified
  via `agent.aget_state(config).tasks[*].interrupts`).
- `Command(resume={"decisions": [{"type": "approve"}]})` /
  `Command(resume={"decisions": [{"type": "reject", "message": "..."}]})`
  passed to `agent.astream_events(...)` resumes the interrupted run exactly
  as `HumanInTheLoopMiddleware.after_model` expects (confirmed both branches
  live): approve -> the tool actually executes (file written) and the model
  is re-invoked with the tool's real result; reject -> the tool does NOT
  execute, and the model's next request carries a synthetic `ToolMessage`
  (`"User rejected the tool call for `write_file` with reason: <message>"`)
  in place of a real tool result.

So a single compiled graph, with one `interrupt_on` entry per mutating tool
whose `when` reads `configurable["hitl_enabled"]`, is sufficient — this is
exactly the ticket's primary (non-fallback) design, and `chat_ws.py` sets
`configurable.hitl_enabled` per turn from `SettingsStore` (see that module's
docstring for the resume-as-a-new-turn machinery).

`_hitl_enabled` intentionally ignores which of the four tools is asking (all
four share the exact same on/off flag — no ticket requirement for per-tool
granularity, which is explicitly out of scope per the issue body) — it only
reads `req.runtime.config`. `_describe_write_file`/`_describe_edit_file`/
`_describe_delete`/`_describe_execute_code` build the human-readable
`description` string surfaced in the `approval_request` frame's `actions[].
description` (`app/api/chat_ws.py`'s `_pending_approval_from_state` reads it
straight off the interrupt's own `ActionRequest.description` — no
recomputation needed there).
"""

from __future__ import annotations

from typing import Any

from deepagents import create_deep_agent
from deepagents.backends import FilesystemBackend
from langchain.agents.middleware import InterruptOnConfig
from langchain.agents.middleware.types import ToolCallRequest
from langchain_core.messages import ToolCall
from langgraph.graph.state import CompiledStateGraph

from app.agent.execute_code_tool import make_execute_code_tool
from app.agent.model_client import build_model
from app.agent.prompts import SYSTEM_PROMPT
from app.agent.web_tools import make_web_fetch_tool, make_web_search_tool
from app.core.config import Settings

# Kept in sync with `app/api/chat_ws.py`'s `_TOOL_CATEGORY_BY_NAME` mapping —
# these are the four tool names the ticket calls "mutating" (category
# `"file"` for the three filesystem ones, `"exec"` for `execute_code`).
MUTATING_TOOL_NAMES: tuple[str, ...] = ("write_file", "edit_file", "delete", "execute_code")


def _hitl_enabled(request: ToolCallRequest) -> bool:
    """`InterruptOnConfig.when` predicate shared by all four mutating tools.

    Reads the per-turn flag `chat_ws.py` sets in `config["configurable"]
    ["hitl_enabled"]` for this run. Defaults to `True` (matching
    `SettingsDocument.hitl_enabled`'s own default) if the caller ever
    invokes the agent without setting it (e.g. a stray direct `.ainvoke()`
    from a test/script) — HITL-on-by-default is the safer failure mode for
    a middleware that guards file writes and code execution.
    """
    config = request.runtime.config or {}
    configurable = config.get("configurable") or {}
    return bool(configurable.get("hitl_enabled", True))


def _describe(tool_call: ToolCall, _state: Any, _runtime: Any) -> str:
    """Human-readable description for the approval card (`InterruptOnConfig.description`).

    A callable (rather than a static string) so each tool gets tailored
    wording instead of one generic sentence for all four — the frontend's
    `ApprovalCard` displays this as-is per the spec's `actions[].description`.
    """
    name = tool_call["name"]
    args = tool_call.get("args") or {}
    if name == "write_file":
        return f"Write file `{args.get('file_path', '?')}`"
    if name == "edit_file":
        return f"Edit file `{args.get('file_path', '?')}`"
    if name == "delete":
        return f"Delete `{args.get('file_path', args.get('path', '?'))}`"
    if name == "execute_code":
        return f"Run command: `{args.get('command', '?')}`"
    return f"Run tool `{name}`"


def _interrupt_on_config() -> InterruptOnConfig:
    return InterruptOnConfig(
        allowed_decisions=["approve", "reject"],
        when=_hitl_enabled,
        description=_describe,
    )


def build_agent(settings: Settings, checkpointer) -> CompiledStateGraph:
    return create_deep_agent(
        model=build_model(settings),
        backend=FilesystemBackend(root_dir=settings.workspace_root, virtual_mode=True),
        system_prompt=SYSTEM_PROMPT,
        tools=[
            make_execute_code_tool(settings),
            make_web_search_tool(settings),
            make_web_fetch_tool(settings),
        ],
        checkpointer=checkpointer,
        interrupt_on={name: _interrupt_on_config() for name in MUTATING_TOOL_NAMES},
    )

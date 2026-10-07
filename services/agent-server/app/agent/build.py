"""Assembles the deep agent: local model client + the platform files backend.

NOTE on `deepagents==0.7.11` vs. the ticket's illustrative snippet: introspection
of the installed package (`inspect.signature(deepagents.create_deep_agent)`)
confirmed `create_deep_agent` accepts `checkpointer` directly as a keyword
argument (typed `Checkpointer | None`, i.e. `None | bool | BaseCheckpointSaver`)
and returns an already-compiled `CompiledStateGraph` wired up with that
checkpointer — no separate `.compile(checkpointer=...)` call is needed, unlike
some other langgraph-based builders.

M11-02: the file tools run on `PlatformFilesBackend`
(`app/agent/platform_files.py`), which acts as the user through the run's
delegation (`configurable["delegation"]`); this service holds no files.
Paths are the platform's virtual paths (`/personal/...`,
`/spaces/<slug>/...`), and the platform is the path guard.

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

M13-02: the app tools (`app/agent/app_tools.py`) are not in `interrupt_on`;
they raise their own approvals from inside the tool, since whether one is
needed depends on what the platform says about the call (see that module).
"""

from __future__ import annotations

from typing import Any

from deepagents import create_deep_agent
from deepagents.backends import CompositeBackend, StateBackend
from deepagents.backends.utils import validate_path
from langchain.agents.middleware import InterruptOnConfig
from langchain.agents.middleware.types import ToolCallRequest
from langchain_core.messages import ToolCall
from langgraph.graph.state import CompiledStateGraph

from app.agent import approvals
from app.agent.app_tools import make_app_tools
from app.agent.execute_code_tool import make_execute_code_tool
from app.agent.model_client import build_model
from app.agent.platform_files import PlatformFilesBackend
from app.agent.prompts import APP_AUTHORING_GUIDE, ROUTINES_GUIDE, SYSTEM_PROMPT
from app.agent.routine_tools import make_routine_tools
from app.agent.symbol_tools import make_symbol_tools
from app.agent.syntax_check import SyntaxCheckMiddleware
from app.agent.tool_errors import CompactToolErrorsMiddleware
from app.agent.tool_notes import ToolNotesMiddleware
from app.agent.web_tools import make_web_fetch_tool, make_web_search_tool
from app.core.config import Settings

# Kept in sync with `app/api/chat_ws.py`'s `_TOOL_CATEGORY_BY_NAME` mapping —
# the tools that need approval (category `"file"` for the filesystem ones,
# `"exec"` for `execute_code`).
MUTATING_TOOL_NAMES: tuple[str, ...] = (
    "write_file",
    "edit_file",
    "replace_symbol",
    "delete",
    "execute_code",
)


def _hitl_enabled(request: ToolCallRequest) -> bool:
    """`InterruptOnConfig.when` predicate shared by all the mutating tools.

    In a chat, the per-turn flag `chat_ws.py` sets in `config["configurable"]
    ["hitl_enabled"]`; in a routine run, its approval mode (M17-05,
    `app.agent.approvals`). HITL-on when neither is set (e.g. a stray direct
    `.ainvoke()` from a test/script) — the safer failure mode for a
    middleware that guards file writes and code execution.
    """
    return approvals.needs_approval(request.runtime.config, request.tool_call["name"])


def _virtual_path(path: Any) -> str:
    """The path as the file tool will send it (`notes.md` -> `/notes.md`)."""
    if not isinstance(path, str):
        return "?"
    try:
        return validate_path(path)
    except ValueError:
        return path


def _describe(tool_call: ToolCall, _state: Any, _runtime: Any) -> str:
    """Human-readable description for the approval card (`InterruptOnConfig.description`).

    A callable (rather than a static string) so each tool gets tailored
    wording instead of one generic sentence for all four — the frontend's
    `ApprovalCard` displays this as-is per the spec's `actions[].description`.
    """
    name = tool_call["name"]
    args = tool_call.get("args") or {}
    if name == "write_file":
        return f"Write file `{_virtual_path(args.get('file_path'))}`"
    if name == "edit_file":
        return f"Edit file `{_virtual_path(args.get('file_path'))}`"
    if name == "replace_symbol":
        return f"Replace `{args.get('name', '?')}` in `{_virtual_path(args.get('file_path'))}`"
    if name == "delete":
        return f"Delete `{_virtual_path(args.get('file_path', args.get('path')))}`"
    if name == "execute_code":
        return f"Run command: `{args.get('command', '?')}`"
    return f"Run tool `{name}`"


def _interrupt_on_config() -> InterruptOnConfig:
    return InterruptOnConfig(
        allowed_decisions=["approve", "reject"],
        when=_hitl_enabled,
        description=_describe,
    )


def build_backend(settings: Settings) -> CompositeBackend:
    """The user's files, plus per-thread state for what middleware sets aside.

    Summarization offloads evicted history to `/conversation_history/` and
    big tool results to `/large_tool_results/`. The platform only has
    `/personal` and `/spaces`, so those live in the thread's checkpoint,
    where the agent can still `read_file` them.
    """
    scratch = StateBackend()
    return CompositeBackend(
        default=PlatformFilesBackend(settings.platform_url),
        routes={"/conversation_history/": scratch, "/large_tool_results/": scratch},
    )


def build_agent(settings: Settings, checkpointer, app_state: Any = None) -> CompiledStateGraph:
    """`app_state` (the app's stores and delegation client) adds the routine tools (M17-07)."""
    routine_tools = make_routine_tools(app_state) if app_state is not None else []
    prompt = SYSTEM_PROMPT + APP_AUTHORING_GUIDE + (ROUTINES_GUIDE if routine_tools else "")
    backend = build_backend(settings)
    return create_deep_agent(
        model=build_model(settings),
        backend=backend,
        middleware=[
            CompactToolErrorsMiddleware(),
            approvals.ReadOnlyRunMiddleware(),
            SyntaxCheckMiddleware(backend),
            ToolNotesMiddleware(),
        ],
        system_prompt=prompt,
        tools=[
            make_execute_code_tool(settings),
            make_web_search_tool(settings),
            make_web_fetch_tool(settings),
            *make_symbol_tools(backend),
            *make_app_tools(settings),
            *routine_tools,
        ],
        checkpointer=checkpointer,
        interrupt_on={name: _interrupt_on_config() for name in MUTATING_TOOL_NAMES},
    )

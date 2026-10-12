"""What the agent may use, and when a call asks first (#342).

The platform holds every user's and the admin's tool settings
(docs/PLATFORM.md §4 "Agent tool permissions"). Each turn fetches its
user's policy with the turn's delegation and carries it in
`configurable["tool_policy"]`: which tools are available, and which run
without asking. Chats and routine runs follow exactly the same rules.

- **Availability.** The tools bound to the model are fixed for a thread
  when its first turn starts (`bound_tools` in the thread's state), so the
  start of the prompt stays the same turn to turn (#341). A tool turned
  off later is refused when called; one turned on later appears in new
  threads. A tool missing from the catalog is never bound.
- **Asking.** A call asks unless the policy allows its tool. Tools in
  `HITL_TOOL_NAMES` ask through `HumanInTheLoopMiddleware`; the app and
  routine tools raise their own approval (`app_tools._ask_user`) because
  their cards need facts from the platform first.
- **Resuming.** A resumed turn answers the approval its calls stopped for,
  so `configurable["pinned_approvals"]` fixes those calls' decision by
  tool call id: the ones on the card ask again (and so take the user's
  answer), the rest of that step runs as it was allowed. Calls after them
  follow the policy fetched for the resumed turn.

With no policy (a direct graph call), every tool is bound and every call
asks.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, NotRequired, Protocol

import httpx
from langchain.agents.middleware import AgentMiddleware, AgentState
from langchain.agents.middleware.types import ModelRequest, ToolCallRequest
from langchain_core.messages import ToolMessage
from langchain_core.runnables import RunnableConfig
from langgraph.config import get_config

from app.agent.tool_catalog import CATALOG, NAMES

# The tools that ask through `HumanInTheLoopMiddleware` (the rest of the
# catalog asks from inside the tool).
SELF_ASKING_TOOLS = frozenset(
    {
        "app_update",
        "app_action",
        "approve_migration",
        "create_routine",
        "update_routine",
        "delete_routine",
        "use_integration",
    }
)
HITL_TOOL_NAMES: tuple[str, ...] = tuple(sorted(NAMES - SELF_ASKING_TOOLS))

_POLICY_TIMEOUT_S = 10.0


class PolicyUnavailable(Exception):
    """The platform couldn't say what applies to this user."""


@dataclass(frozen=True)
class ToolPolicy:
    available: frozenset[str]
    allowed: frozenset[str]

    @classmethod
    def from_platform(cls, body: Any) -> ToolPolicy:
        tools = body.get("tools") if isinstance(body, dict) else None
        if not isinstance(tools, dict):
            raise PolicyUnavailable("unexpected policy response")
        available = {n for n, t in tools.items() if isinstance(t, dict) and t.get("available")}
        allowed = {n for n in available if tools[n].get("permission") == "allow"}
        return cls(frozenset(available & NAMES), frozenset(allowed & NAMES))

    @classmethod
    def allow_all(cls) -> ToolPolicy:
        return cls(NAMES, NAMES)


DEFAULT_POLICY = ToolPolicy(NAMES, frozenset(t.name for t in CATALOG if t.read_only))


class ToolPolicies(Protocol):
    async def for_turn(self, delegation: Any) -> ToolPolicy | None: ...


class PlatformToolPolicies:
    """The turn's policy from the platform, asked with the turn's delegation."""

    def __init__(self, platform_url: str) -> None:
        self._platform_url = platform_url

    async def for_turn(self, delegation: Any) -> ToolPolicy | None:
        token = getattr(delegation, "token", None)
        if not isinstance(token, str) or not token:
            return None
        return await fetch_policy(self._platform_url, token)


@dataclass(frozen=True)
class StaticToolPolicies:
    """One policy for every turn (tests, evals)."""

    policy: ToolPolicy = DEFAULT_POLICY

    async def for_turn(self, delegation: Any) -> ToolPolicy | None:
        return self.policy


async def fetch_policy(platform_url: str, delegation_token: str) -> ToolPolicy:
    try:
        async with httpx.AsyncClient(timeout=_POLICY_TIMEOUT_S) as client:
            response = await client.get(
                f"{platform_url}/api/platform/tools/policy",
                headers={"Authorization": f"Bearer {delegation_token}"},
            )
    except httpx.HTTPError as exc:
        raise PolicyUnavailable(repr(exc)) from exc
    if response.status_code != 200:
        raise PolicyUnavailable(f"HTTP {response.status_code}")
    return ToolPolicy.from_platform(response.json())


def _configurable(config: RunnableConfig | None) -> dict:
    return (config or {}).get("configurable") or {}


def policy_of(config: RunnableConfig | None) -> ToolPolicy | None:
    policy = _configurable(config).get("tool_policy")
    return policy if isinstance(policy, ToolPolicy) else None


def available_now(config: RunnableConfig | None) -> frozenset[str]:
    policy = policy_of(config)
    return NAMES if policy is None else policy.available


def needs_approval(config: RunnableConfig | None, tool_name: str, tool_call_id: str | None) -> bool:
    pinned = _configurable(config).get("pinned_approvals")
    if isinstance(pinned, dict) and tool_call_id in pinned:
        return bool(pinned[tool_call_id])
    policy = policy_of(config)
    return policy is None or tool_name not in policy.allowed


def in_routine_run(config: RunnableConfig | None) -> bool:
    return _configurable(config).get("routine_run") is True


def _current_config() -> RunnableConfig | None:
    try:
        return get_config()
    except RuntimeError:
        return None


def _tool_name(tool: Any) -> str | None:
    name = tool.get("name") if isinstance(tool, dict) else getattr(tool, "name", None)
    return name if isinstance(name, str) else None


class _PolicyState(AgentState):
    bound_tools: NotRequired[list[str]]


def _off_error(name: str) -> str:
    return (
        f"Error: {name} is turned off in the user's settings, so it can't be used. "
        "Don't retry; say what you would have done."
    )


class ToolPolicyMiddleware(AgentMiddleware):
    """Binds the thread's tools and refuses calls to tools that aren't available."""

    state_schema = _PolicyState

    def _bind(self, state: Any) -> dict[str, Any] | None:
        if state.get("bound_tools") is not None:
            return None
        return {"bound_tools": sorted(available_now(_current_config()))}

    def before_agent(self, state: Any, runtime: Any) -> dict[str, Any] | None:
        return self._bind(state)

    async def abefore_agent(self, state: Any, runtime: Any) -> dict[str, Any] | None:
        return self._bind(state)

    def _filtered(self, request: ModelRequest) -> ModelRequest:
        bound = request.state.get("bound_tools")
        names = set(bound) if bound is not None else available_now(_current_config())
        return request.override(tools=[t for t in request.tools if _tool_name(t) in names])

    def wrap_model_call(self, request: ModelRequest, handler: Callable[[ModelRequest], Any]) -> Any:
        return handler(self._filtered(request))

    async def awrap_model_call(
        self, request: ModelRequest, handler: Callable[[ModelRequest], Awaitable[Any]]
    ) -> Any:
        return await handler(self._filtered(request))

    def _refusal(self, request: ToolCallRequest) -> ToolMessage | None:
        call = request.tool_call
        name = call["name"]
        bound = (request.state or {}).get("bound_tools")
        config = request.runtime.config if request.runtime is not None else _current_config()
        if name in available_now(config) and (bound is None or name in bound):
            return None
        return ToolMessage(
            content=_off_error(name), tool_call_id=call["id"], name=name, status="error"
        )

    def wrap_tool_call(
        self, request: ToolCallRequest, handler: Callable[[ToolCallRequest], Any]
    ) -> Any:
        return self._refusal(request) or handler(request)

    async def awrap_tool_call(
        self, request: ToolCallRequest, handler: Callable[[ToolCallRequest], Awaitable[Any]]
    ) -> Any:
        return self._refusal(request) or await handler(request)

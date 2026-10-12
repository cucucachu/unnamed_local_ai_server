"""The built-in tools, as people see them in Settings → Agent permissions (#342).

agent-server registers this list with the platform at startup
(`PUT /internal/tool-catalog`); the platform stores each user's and the
admin's settings against these names (docs/PLATFORM.md §4 "Agent tool
permissions"). `read_only` marks the tools we can guarantee change nothing,
which start as allowed for everyone; every other tool starts as asking.

Every tool the agent can call must be listed here: a tool that isn't is
never shown to the model (`app.agent.approvals`).
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import asdict, dataclass

import httpx

logger = logging.getLogger(__name__)

REGISTER_RETRY_S = 5.0


@dataclass(frozen=True)
class CatalogTool:
    name: str
    label: str
    group: str
    read_only: bool
    description: str


def _tools(group: str, *entries: tuple[str, str, bool, str]) -> list[CatalogTool]:
    return [CatalogTool(name, label, group, ro, about) for name, label, ro, about in entries]


CATALOG: tuple[CatalogTool, ...] = (
    *_tools(
        "Files",
        ("ls", "List folders", True, "See what's in a folder."),
        ("read_file", "Read files", True, "Open and read a file."),
        ("glob", "Find files", True, "Find files by name."),
        ("grep", "Search in files", True, "Search for text inside files."),
        ("outline", "Outline code", True, "List the functions and components in a code file."),
        ("write_file", "Write files", False, "Create a file, or replace one's contents."),
        ("edit_file", "Edit files", False, "Change part of a file."),
        (
            "replace_symbol",
            "Replace code",
            False,
            "Replace a function or component in a code file.",
        ),
        ("delete", "Delete files", False, "Delete a file or folder permanently."),
    ),
    *_tools(
        "Code",
        (
            "execute_code",
            "Run code",
            False,
            "Run commands in a sandbox that can change your files.",
        ),
    ),
    *_tools(
        "Web",
        ("web_search", "Search the web", True, "Look things up on the web."),
        ("web_fetch", "Read web pages", True, "Open and read a web page."),
    ),
    *_tools(
        "Apps",
        ("list_apps", "List apps", True, "See your apps and what they can do."),
        ("app_query", "Read app data", True, "Look up data in an app."),
        ("create_app", "Create apps", False, "Start a new app from a template."),
        ("build_app", "Build apps", False, "Build an app's code and switch it to the new version."),
        ("app_update", "Change app data", False, "Add, change or remove data in an app."),
        ("app_action", "Run app actions", False, "Run an app's actions, such as moving a file."),
        (
            "approve_migration",
            "Apply destructive app changes",
            False,
            "Apply an app database change that drops data (a snapshot is taken first).",
        ),
    ),
    *_tools(
        "Routines",
        ("current_time", "Check the time", True, "Read the current date and time."),
        ("list_routines", "List routines", True, "See your routines."),
        ("create_routine", "Create routines", False, "Set up a new routine."),
        ("update_routine", "Change routines", False, "Change, pause or resume a routine."),
        ("delete_routine", "Delete routines", False, "Delete a routine."),
    ),
    *_tools(
        "Integrations",
        # Each integration tool also has its own setting (Settings → Integrations);
        # a call runs unasked only if both allow.
        (
            "open_integration",
            "Look at integrations",
            True,
            "See what a connected service, such as Home Assistant, offers.",
        ),
        (
            "use_integration",
            "Use integrations",
            True,
            "Use a connected service's tools; each tool's own setting applies too.",
        ),
        (
            "read_integration_resource",
            "Read from integrations",
            True,
            "Read information a connected service provides.",
        ),
        (
            "get_integration_prompt",
            "Get integration guidance",
            True,
            "Get a connected service's own guidance for a task.",
        ),
    ),
    *_tools(
        "Planning",
        # Delegating changes nothing by itself: the helper's own calls
        # follow the same settings.
        ("task", "Use a helper", True, "Hand a self-contained job to a helper agent."),
    ),
)

NAMES: frozenset[str] = frozenset(t.name for t in CATALOG)


def payload() -> dict:
    return {"tools": [asdict(t) for t in CATALOG]}


async def register(platform_url: str, agent_token: str) -> None:
    """Send the catalog to the platform, retrying until it answers."""
    if not agent_token:
        logger.warning("PLATFORM_AGENT_TOKEN unset: the tool catalog isn't registered")
        return
    while True:
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.put(
                    f"{platform_url}/internal/tool-catalog",
                    json=payload(),
                    headers={"Authorization": f"Bearer {agent_token}"},
                )
            if response.status_code == 204:
                logger.info("tool catalog registered (%d tools)", len(CATALOG))
                return
            logger.warning("tool catalog: HTTP %s %s", response.status_code, response.text[:200])
        except httpx.HTTPError as exc:
            logger.warning("tool catalog: platform unreachable (%r); retrying", exc)
        await asyncio.sleep(REGISTER_RETRY_S)

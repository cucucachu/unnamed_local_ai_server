"""Agent tool permissions (#342, docs/PLATFORM.md §4 "Agent tool permissions").

Every tool in the catalog has two controls, each set by the admin (for
everyone) and by each user (for themselves):

- **availability**: on or off. A tool is available only if both have it on;
  agent-server doesn't show an unavailable tool to the model at all.
- **permission**: ask or allow. The admin's is a ceiling (default allow); the
  user's defaults to ask, or to allow for a tool the catalog marks read-only.
  A call runs without asking only if both say allow.

Admin and user rows are stored apart. A user row more permissive than the
admin's is a valid stored state (the admin tightened the tool later); it is
never rewritten, `evaluate` lets the admin's win, and nothing here shows a
user a setting the admin doesn't allow. The admin is trusted: no tool has a
floor in code.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal
from uuid import UUID

from psycopg import AsyncConnection

from app.core import audit
from app.core.errors import InvalidInput
from app.core.principal import Principal

Permission = Literal["ask", "allow"]
MAX_CATALOG = 200


@dataclass(frozen=True)
class Tool:
    name: str
    label: str
    description: str
    group: str
    read_only: bool


@dataclass(frozen=True)
class Effective:
    """What applies to one user and tool."""

    available: bool
    permission: Permission
    # Locked: the admin's setting decides, so the user can't choose.
    enabled_locked: bool
    permission_locked: bool


def evaluate(tool: Tool, admin: dict | None, mine: dict | None) -> Effective:
    admin, mine = admin or {}, mine or {}
    admin_on = admin.get("enabled") is not False
    user_on = mine.get("enabled") is not False
    ceiling_allows = admin.get("ceiling", "allow") != "ask"
    default: Permission = "allow" if tool.read_only else "ask"
    user_allows = (mine.get("permission") or default) == "allow"
    return Effective(
        available=admin_on and user_on,
        permission="allow" if ceiling_allows and user_allows else "ask",
        enabled_locked=not admin_on,
        permission_locked=not ceiling_allows,
    )


async def catalog(conn: AsyncConnection) -> list[Tool]:
    cur = await conn.execute(
        "SELECT name, label, description, tool_group, read_only FROM agent_tools ORDER BY position"
    )
    return [
        Tool(r["name"], r["label"], r["description"], r["tool_group"], r["read_only"])
        for r in await cur.fetchall()
    ]


async def replace_catalog(conn: AsyncConnection, tools: list[Tool]) -> None:
    """agent-server's catalog, as registered at its startup. Settings are untouched."""
    names = [t.name for t in tools]
    if len(set(names)) != len(names):
        raise InvalidInput("duplicate_tool")
    async with conn.transaction():
        await conn.execute("DELETE FROM agent_tools WHERE NOT (name = ANY(%s))", (names,))
        for position, t in enumerate(tools):
            await conn.execute(
                "INSERT INTO agent_tools (name, label, description, tool_group, read_only, position) "
                "VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT (name) DO UPDATE SET "
                "label = EXCLUDED.label, description = EXCLUDED.description, "
                "tool_group = EXCLUDED.tool_group, read_only = EXCLUDED.read_only, "
                "position = EXCLUDED.position, updated_at = now()",
                (t.name, t.label, t.description, t.group, t.read_only, position),
            )


async def _admin_rows(conn: AsyncConnection) -> dict[str, dict]:
    cur = await conn.execute("SELECT tool, enabled, ceiling FROM agent_tool_admin_settings")
    return {r["tool"]: r for r in await cur.fetchall()}


async def _user_rows(conn: AsyncConnection, user_id: UUID) -> dict[str, dict]:
    cur = await conn.execute(
        "SELECT tool, enabled, permission FROM agent_tool_user_settings WHERE user_id = %s",
        (user_id,),
    )
    return {r["tool"]: r for r in await cur.fetchall()}


def _tool_out(tool: Tool) -> dict[str, Any]:
    return {
        "name": tool.name,
        "label": tool.label,
        "description": tool.description,
        "group": tool.group,
        "read_only": tool.read_only,
    }


async def for_user(conn: AsyncConnection, user_id: UUID) -> list[tuple[Tool, Effective]]:
    tools = await catalog(conn)
    admin, mine = await _admin_rows(conn), await _user_rows(conn, user_id)
    return [(t, evaluate(t, admin.get(t.name), mine.get(t.name))) for t in tools]


async def user_view(conn: AsyncConnection, user_id: UUID) -> list[dict[str, Any]]:
    """A user's list: effective settings only, with what the admin locks."""
    return [
        {
            **_tool_out(t),
            "enabled": e.available,
            "permission": e.permission,
            "enabled_locked": e.enabled_locked,
            "permission_locked": e.permission_locked,
        }
        for t, e in await for_user(conn, user_id)
    ]


async def policy(conn: AsyncConnection, user_id: UUID) -> dict[str, dict[str, Any]]:
    """What agent-server needs for one user's turn."""
    return {
        t.name: {
            "available": e.available,
            "permission": e.permission,
            "can_always_allow": not e.permission_locked,
        }
        for t, e in await for_user(conn, user_id)
    }


async def admin_view(conn: AsyncConnection) -> list[dict[str, Any]]:
    admin = await _admin_rows(conn)
    out = []
    for t in await catalog(conn):
        row = admin.get(t.name) or {}
        out.append(
            {
                **_tool_out(t),
                "enabled": row.get("enabled") is not False,
                "ceiling": row.get("ceiling") or "allow",
            }
        )
    return out


async def _known(conn: AsyncConnection, names: list[str]) -> dict[str, Tool]:
    tools = {t.name: t for t in await catalog(conn)}
    for name in names:
        if name not in tools:
            raise InvalidInput("unknown_tool")
    return tools


async def change_mine(
    conn: AsyncConnection, principal: Principal, changes: list[dict[str, Any]]
) -> None:
    """Apply a user's own changes, all or nothing.

    A value the admin's setting doesn't allow (turning on a tool the admin
    turned off, allowing a tool whose ceiling is ask) is refused: the UI
    never offers it.
    """
    tools = await _known(conn, [c["name"] for c in changes])
    admin = await _admin_rows(conn)
    for c in changes:
        row = admin.get(c["name"]) or {}
        if c.get("enabled") is True and row.get("enabled") is False:
            raise InvalidInput("locked_by_admin")
        if c.get("permission") == "allow" and row.get("ceiling") == "ask":
            raise InvalidInput("locked_by_admin")
    async with conn.transaction():
        for c in changes:
            await conn.execute(
                "INSERT INTO agent_tool_user_settings (user_id, tool, enabled, permission) "
                "VALUES (%s, %s, %s, %s) ON CONFLICT (user_id, tool) DO UPDATE SET "
                "enabled = COALESCE(EXCLUDED.enabled, agent_tool_user_settings.enabled), "
                "permission = COALESCE(EXCLUDED.permission, agent_tool_user_settings.permission), "
                "updated_at = now()",
                (principal.user_id, c["name"], c.get("enabled"), c.get("permission")),
            )
        await audit.record(
            conn,
            principal,
            "tools.settings_changed",
            _summary("Changed my tool settings", changes, tools),
            target_type="user",
            target_id=principal.user_id,
            detail={"changes": changes},
        )


async def change_admin(
    conn: AsyncConnection, principal: Principal, changes: list[dict[str, Any]]
) -> None:
    tools = await _known(conn, [c["name"] for c in changes])
    async with conn.transaction():
        for c in changes:
            await conn.execute(
                "INSERT INTO agent_tool_admin_settings (tool, enabled, ceiling, updated_by) "
                "VALUES (%s, %s, %s, %s) ON CONFLICT (tool) DO UPDATE SET "
                "enabled = COALESCE(EXCLUDED.enabled, agent_tool_admin_settings.enabled), "
                "ceiling = COALESCE(EXCLUDED.ceiling, agent_tool_admin_settings.ceiling), "
                "updated_by = EXCLUDED.updated_by, updated_at = now()",
                (c["name"], c.get("enabled"), c.get("ceiling"), principal.user_id),
            )
        await audit.record(
            conn,
            principal,
            "tools.admin_changed",
            _summary("Changed tool settings for everyone", changes, tools),
            target_type="tools",
            detail={"changes": changes},
        )


def _summary(head: str, changes: list[dict[str, Any]], tools: dict[str, Tool]) -> str:
    parts = []
    for c in changes:
        values = [
            ("on" if c["enabled"] else "off") if c.get("enabled") is not None else None,
            c.get("permission") or c.get("ceiling"),
        ]
        parts.append(f"{tools[c['name']].label}: {', '.join(v for v in values if v)}")
    return f"{head}: " + "; ".join(parts)

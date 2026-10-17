"""Delivered routines' admin switch (M21-08, docs/MEMORY.md §4.4).

agent-server delivers some routines to every user (the Memory routine) and
registers their keys and names at startup (`replace_catalog`). The admin
can turn each one off for the whole household; a missing row means on.
While it's off, agent-server runs none of them. Each user's own on/off is
agent-server's and is kept as it is.

Anyone signed in, and their agent, can read the switches; only the admin,
in person and stepped up, changes them (the admin router), audited.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from psycopg import AsyncConnection

from app.core import audit
from app.core.errors import InvalidInput, NotFound
from app.core.principal import Principal

MAX_CATALOG = 50


@dataclass(frozen=True)
class Routine:
    key: str
    name: str
    description: str


async def replace_catalog(conn: AsyncConnection, routines: list[Routine]) -> None:
    """agent-server's list, as registered at its startup. The switches are untouched."""
    keys = [r.key for r in routines]
    if len(set(keys)) != len(keys):
        raise InvalidInput("duplicate_routine")
    async with conn.transaction():
        await conn.execute("DELETE FROM delivered_routines WHERE NOT (key = ANY(%s))", (keys,))
        for position, r in enumerate(routines):
            await conn.execute(
                "INSERT INTO delivered_routines (key, name, description, position) "
                "VALUES (%s, %s, %s, %s) ON CONFLICT (key) DO UPDATE SET "
                "name = EXCLUDED.name, description = EXCLUDED.description, "
                "position = EXCLUDED.position, updated_at = now()",
                (r.key, r.name, r.description, position),
            )


async def view(conn: AsyncConnection) -> list[dict[str, Any]]:
    """Each registered routine with the admin's switch, in agent-server's order."""
    cur = await conn.execute(
        "SELECT r.key, r.name, r.description, COALESCE(s.enabled, true) AS enabled_by_admin "
        "FROM delivered_routines r LEFT JOIN delivered_routine_admin_settings s USING (key) "
        "ORDER BY r.position"
    )
    return list(await cur.fetchall())


async def set_admin(
    conn: AsyncConnection, principal: Principal, key: str, enabled: bool
) -> dict[str, Any]:
    cur = await conn.execute("SELECT name FROM delivered_routines WHERE key = %s", (key,))
    row = await cur.fetchone()
    if row is None:
        raise NotFound("unknown_routine")
    async with conn.transaction():
        await conn.execute(
            "INSERT INTO delivered_routine_admin_settings (key, enabled, updated_by) "
            "VALUES (%s, %s, %s) ON CONFLICT (key) DO UPDATE SET "
            "enabled = EXCLUDED.enabled, updated_by = EXCLUDED.updated_by, updated_at = now()",
            (key, enabled, principal.user_id),
        )
        await audit.record(
            conn,
            principal,
            "delivered_routines.admin_changed",
            f"Turned the {row['name']} routine {'on' if enabled else 'off'} for everyone",
            target_type="delivered_routine",
            target_id=key,
            detail={"key": key, "enabled": enabled},
        )
    return next(r for r in await view(conn) if r["key"] == key)

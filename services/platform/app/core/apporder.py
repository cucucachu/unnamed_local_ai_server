"""A user's arrangement of a space's home page (#465), see migration 0024."""

from __future__ import annotations

from uuid import UUID

from psycopg import AsyncConnection
from psycopg.types.json import Jsonb

from app.core import spaces
from app.core.principal import Principal

MAX_ITEMS = 500


async def get_order(conn: AsyncConnection, principal: Principal, space_id: UUID) -> list[str]:
    """The caller's order for the space, or [] if they never arranged it. Needs read access."""
    await spaces.authorize_space(conn, principal, space_id, "read")
    cur = await conn.execute(
        "SELECT items FROM app_orders WHERE user_id = %s AND space_id = %s",
        (principal.user_id, space_id),
    )
    row = await cur.fetchone()
    return list(row["items"]) if row else []


async def set_order(
    conn: AsyncConnection, principal: Principal, space_id: UUID, items: list[str]
) -> list[str]:
    """Replace the caller's order; duplicates keep their first place. Viewers may arrange too."""
    await spaces.authorize_space(conn, principal, space_id, "read")
    unique = list(dict.fromkeys(items))
    await conn.execute(
        "INSERT INTO app_orders (user_id, space_id, items) VALUES (%s, %s, %s) "
        "ON CONFLICT (user_id, space_id) DO UPDATE SET items = EXCLUDED.items, updated_at = now()",
        (principal.user_id, space_id, Jsonb(unique)),
    )
    return unique

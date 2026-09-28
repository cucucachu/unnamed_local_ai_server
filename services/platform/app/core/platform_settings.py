"""Platform-wide settings stored in `platform_state` (M15-05 `public_https`).

The DB flag is the source of truth. Compose has no `PUBLIC_HTTPS` env.
Cached on `app.state.public_https` at startup and updated on PATCH.
"""

from __future__ import annotations

from psycopg import AsyncConnection
from psycopg.types.json import Jsonb

PUBLIC_HTTPS_KEY = "public_https"


async def get_public_https(conn: AsyncConnection) -> bool:
    cur = await conn.execute("SELECT value FROM platform_state WHERE key = %s", (PUBLIC_HTTPS_KEY,))
    row = await cur.fetchone()
    if row is None:
        return False
    return row["value"] is True


async def set_public_https(conn: AsyncConnection, enabled: bool) -> bool:
    await conn.execute(
        "INSERT INTO platform_state (key, value) VALUES (%s, %s) "
        "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now()",
        (PUBLIC_HTTPS_KEY, Jsonb(enabled)),
    )
    return enabled

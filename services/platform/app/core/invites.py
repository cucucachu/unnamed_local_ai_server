"""Single-use, 7-day invite tokens (docs/PLATFORM.md §4). Only SHA-256 digests are stored."""

from __future__ import annotations

from datetime import timedelta
from typing import Any
from uuid import UUID

from psycopg import AsyncConnection

from app.core import sessions, users
from app.core.errors import InvalidInput, NotFound, Unauthorized

TOKEN_PREFIX = "hi_"
INVITE_TTL = timedelta(days=7)
MAX_LABEL_LENGTH = 64

Row = dict[str, Any]

_COLUMNS = (
    "id, label, created_by, created_at, expires_at, used_at, used_by, revoked_at, "
    "CASE WHEN used_at IS NOT NULL THEN 'used' WHEN revoked_at IS NOT NULL THEN 'revoked' "
    "WHEN expires_at <= now() THEN 'expired' ELSE 'pending' END AS status"
)


def _label(label: str | None) -> str | None:
    if label is None:
        return None
    label = label.strip()
    if len(label) > MAX_LABEL_LENGTH or not label.isprintable():
        raise InvalidInput("invalid_label")
    return label or None


async def create_invite(
    conn: AsyncConnection, created_by: UUID, label: str | None = None
) -> tuple[str, Row]:
    token = sessions.new_token(TOKEN_PREFIX)
    cur = await conn.execute(
        "INSERT INTO invites (token_hash, label, created_by, expires_at) "
        f"VALUES (%s, %s, %s, now() + %s) RETURNING {_COLUMNS}",
        (sessions.hash_token(token), _label(label), created_by, INVITE_TTL),
    )
    return token, await cur.fetchone()


async def list_invites(conn: AsyncConnection) -> list[Row]:
    cur = await conn.execute(f"SELECT {_COLUMNS} FROM invites ORDER BY created_at DESC")
    return await cur.fetchall()


async def revoke_invite(conn: AsyncConnection, invite_id: UUID) -> None:
    """Revoke a pending invite; a no-op for one already used/revoked/expired."""
    cur = await conn.execute(
        "UPDATE invites SET revoked_at = COALESCE(revoked_at, CASE WHEN used_at IS NULL "
        "THEN now() END) WHERE id = %s",
        (invite_id,),
    )
    if cur.rowcount == 0:
        raise NotFound("not_found")


async def accept_invite(
    conn: AsyncConnection,
    token: str,
    *,
    username: str,
    display_name: str,
    password: str,
    device_label: str | None = None,
) -> tuple[Row, str]:
    """Create a member from a pending invite and log them in; returns (user, session token).

    Claiming the invite and creating the user share one transaction, so a
    taken username leaves the invite usable.
    """
    new_user = await users.prepare_user(username, display_name, password, "member")
    async with conn.transaction():
        cur = await conn.execute(
            "UPDATE invites SET used_at = now() WHERE token_hash = %s AND used_at IS NULL "
            "AND revoked_at IS NULL AND expires_at > now() RETURNING id",
            (sessions.hash_token(token),),
        )
        invite = await cur.fetchone()
        if invite is None:
            raise Unauthorized("invalid_invite")
        user = await users.insert_user(conn, new_user)
        await conn.execute(
            "UPDATE invites SET used_by = %s WHERE id = %s", (user["id"], invite["id"])
        )
        session_token, _ = await sessions.create_session(conn, user["id"], device_label)
    return user, session_token

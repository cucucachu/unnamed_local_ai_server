"""Opaque session tokens (docs/PLATFORM.md §4).

A token is `hs_` + 32 random bytes (urlsafe base64). Only its SHA-256 is
stored. Sessions expire 30 days after they were last seen (sliding); step-up
opens a 5-minute window for admin actions.
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

from psycopg import AsyncConnection

TOKEN_PREFIX = "hs_"
SESSION_TTL = timedelta(days=30)
STEP_UP_TTL = timedelta(minutes=5)
# `last_seen_at` / sliding expiry are bumped at most this often per session,
# so the hot verify path usually doesn't write.
LAST_SEEN_GRANULARITY = timedelta(seconds=60)
MAX_DEVICE_LABEL_LENGTH = 64

Row = dict[str, Any]

_ACTIVE = "s.revoked_at IS NULL AND s.expires_at > now() AND u.disabled_at IS NULL"


def new_token(prefix: str = TOKEN_PREFIX) -> str:
    return prefix + secrets.token_urlsafe(32)


def hash_token(token: str) -> bytes:
    return hashlib.sha256(token.encode()).digest()


def _device_label(label: str | None) -> str | None:
    if label is None:
        return None
    label = "".join(ch for ch in label.strip() if ch.isprintable())[:MAX_DEVICE_LABEL_LENGTH]
    return label or None


async def create_session(
    conn: AsyncConnection,
    user_id: UUID,
    device_label: str | None = None,
    *,
    device_id: UUID | None = None,
    host_device_id: UUID | None = None,
) -> tuple[str, Row]:
    token = new_token()
    cur = await conn.execute(
        "INSERT INTO sessions "
        "(token_hash, user_id, device_label, device_id, host_device_id, expires_at) "
        "VALUES (%s, %s, %s, %s, %s, now() + %s) RETURNING id, created_at, expires_at",
        (
            hash_token(token),
            user_id,
            _device_label(device_label),
            device_id,
            host_device_id,
            SESSION_TTL,
        ),
    )
    return token, await cur.fetchone()


async def resolve_token(conn: AsyncConnection, token: str) -> Row | None:
    """The active session + enabled user for `token`, sliding its expiry; else None.

    One statement on the `token_hash` unique index: the data-modifying CTE
    bumps `last_seen_at`/`expires_at` when they're older than
    `LAST_SEEN_GRANULARITY`.
    """
    cur = await conn.execute(
        "WITH hit AS ("
        "  SELECT s.id AS session_id, u.id, u.username, u.display_name, u.role, u.uid,"
        "         (u.totp_secret IS NOT NULL) AS totp_enabled, u.require_passkeys,"
        "         u.disabled_at, u.created_at"
        "  FROM sessions s JOIN users u ON u.id = s.user_id"
        f" WHERE s.token_hash = %(hash)s AND {_ACTIVE}"
        "), bump AS ("
        "  UPDATE sessions SET last_seen_at = now(), expires_at = now() + %(ttl)s"
        "  WHERE id = (SELECT session_id FROM hit) AND last_seen_at < now() - %(granularity)s"
        ") SELECT * FROM hit",
        {"hash": hash_token(token), "ttl": SESSION_TTL, "granularity": LAST_SEEN_GRANULARITY},
    )
    return await cur.fetchone()


async def load_active(conn: AsyncConnection, session_id: UUID, user_id: UUID) -> Row | None:
    """Re-check a session named by a verified JWT: still active, user still enabled."""
    cur = await conn.execute(
        "SELECT s.id AS session_id, (s.stepped_up_until IS NOT NULL"
        "       AND s.stepped_up_until > now()) AS stepped_up,"
        "       u.id, u.username, u.display_name, u.role, u.uid"
        " FROM sessions s JOIN users u ON u.id = s.user_id"
        f" WHERE s.id = %s AND s.user_id = %s AND {_ACTIVE}",
        (session_id, user_id),
    )
    return await cur.fetchone()


async def step_up(conn: AsyncConnection, session_id: UUID) -> datetime:
    cur = await conn.execute(
        "UPDATE sessions SET stepped_up_until = now() + %s WHERE id = %s "
        "RETURNING stepped_up_until",
        (STEP_UP_TTL, session_id),
    )
    return (await cur.fetchone())["stepped_up_until"]


async def revoke_session(conn: AsyncConnection, session_id: UUID, user_id: UUID) -> bool:
    cur = await conn.execute(
        "UPDATE sessions SET revoked_at = now() "
        "WHERE id = %s AND user_id = %s AND revoked_at IS NULL AND expires_at > now()",
        (session_id, user_id),
    )
    return cur.rowcount == 1


async def revoke_user_sessions(
    conn: AsyncConnection, user_id: UUID, *, except_session_id: UUID | None = None
) -> None:
    await conn.execute(
        "UPDATE sessions SET revoked_at = now() "
        "WHERE user_id = %s AND revoked_at IS NULL AND id IS DISTINCT FROM %s",
        (user_id, except_session_id),
    )


async def list_user_sessions(conn: AsyncConnection, user_id: UUID) -> list[Row]:
    cur = await conn.execute(
        "SELECT id, device_label, created_at, last_seen_at, expires_at FROM sessions "
        "WHERE user_id = %s AND revoked_at IS NULL AND expires_at > now() "
        "ORDER BY last_seen_at DESC",
        (user_id,),
    )
    return await cur.fetchall()

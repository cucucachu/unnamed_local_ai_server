"""Delegation tokens: an agent run acting as its user (docs/PLATFORM.md §4 "Delegation token").

agent-server exchanges the caller's identity token (`act=user`, 5 min) for a
delegation (`act=agent`, `thr=<thread_id>`, 15 min) and refreshes it while
the run - or the chat socket that drives it - is alive. Both mint only while
the named session is active and its user enabled; the role is re-read from
the database. The refresh grace covers a refresh that lands just after
expiry (a busy event loop, a platform restart); an older delegation is dead.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from psycopg import AsyncConnection

from app.core import sessions, spaces, vfs
from app.core.errors import InvalidInput, NotFound, Unauthorized
from app.core.tokens import TokenError, TokenService

DELEGATION_TTL = timedelta(minutes=15)
REFRESH_GRACE = timedelta(minutes=5)
_THREAD_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


async def _issue(
    conn: AsyncConnection, tokens: TokenService, claims: dict[str, Any], thread_id: str
) -> tuple[str, datetime]:
    try:
        user_id, session_id = UUID(str(claims["sub"])), UUID(str(claims.get("sid")))
    except ValueError as exc:
        raise Unauthorized("unauthenticated") from exc
    return await issue(conn, tokens, user_id, session_id, thread_id)


async def issue(
    conn: AsyncConnection, tokens: TokenService, user_id: UUID, session_id: UUID, thread_id: str
) -> tuple[str, datetime]:
    """A delegation for `thread_id` on an active session (a chat's, or a routine grant)."""
    if not _THREAD_ID.fullmatch(thread_id):
        raise InvalidInput("invalid_thread_id")
    row = await sessions.load_active(conn, session_id, user_id)
    if row is None:
        raise Unauthorized("unauthenticated")
    now = datetime.now(UTC).replace(microsecond=0)
    payload = {
        "sub": str(user_id),
        "sid": str(session_id),
        "role": row["role"],
        "act": "agent",
        "thr": thread_id,
    }
    return tokens.issue_token(payload, DELEGATION_TTL, now=now), now + DELEGATION_TTL


async def exchange(
    conn: AsyncConnection, tokens: TokenService, identity_token: str, thread_id: str
) -> tuple[str, datetime]:
    """A delegation for `thread_id` from a still-valid identity token."""
    if not _THREAD_ID.fullmatch(thread_id):
        raise InvalidInput("invalid_thread_id")
    try:
        claims = tokens.verify_token(identity_token, act="user")
    except TokenError as exc:
        raise Unauthorized("unauthenticated") from exc
    return await _issue(conn, tokens, claims, thread_id)


async def refresh(conn: AsyncConnection, tokens: TokenService, token: str) -> tuple[str, datetime]:
    """A fresh delegation for the same user, session, and thread."""
    try:
        claims = tokens.verify_token(token, act="agent", leeway=REFRESH_GRACE)
    except TokenError as exc:
        raise Unauthorized("unauthenticated") from exc
    thread_id = claims.get("thr")
    if not isinstance(thread_id, str) or not _THREAD_ID.fullmatch(thread_id):
        raise Unauthorized("unauthenticated")
    return await _issue(conn, tokens, claims, thread_id)


async def space_role(
    conn: AsyncConnection, tokens: TokenService, identity_token: str, space_path: str
) -> tuple[str, str]:
    """`(canonical path, role)` of the identity's user in an active space they belong to.

    Only a space root names a space: `/personal` or `/spaces/<slug>`.
    """
    try:
        claims = tokens.verify_token(identity_token, act="user")
        user_id, session_id = UUID(str(claims["sub"])), UUID(str(claims.get("sid")))
    except (TokenError, ValueError) as exc:
        raise Unauthorized("unauthenticated") from exc
    if await sessions.load_active(conn, session_id, user_id) is None:
        raise Unauthorized("unauthenticated")
    space, role = await member_space(conn, user_id, space_path)
    return canonical_space(space), role


def canonical_space(space: spaces.Row) -> str:
    return f"/{vfs.PERSONAL}" if space["kind"] == "personal" else f"/{vfs.SPACES}/{space['slug']}"


async def member_space(
    conn: AsyncConnection, user_id: UUID, space_path: str
) -> tuple[spaces.Row, str]:
    """`(space, role)` for a space root path the user belongs to; NotFound otherwise."""
    parts = vfs.parse(space_path)
    if parts == (vfs.PERSONAL,):
        space = await spaces.get_personal_space(conn, user_id)
    elif len(parts) == 2 and parts[0] == vfs.SPACES:
        space = await spaces.get_space_by_slug(conn, parts[1])
        if space["kind"] != "shared":
            raise NotFound("not_found")
    else:
        raise InvalidInput("invalid_space")
    cur = await conn.execute(
        "SELECT role FROM space_members WHERE space_id = %s AND user_id = %s",
        (space["id"], user_id),
    )
    row = await cur.fetchone()
    if row is None:
        raise NotFound("not_found")
    return space, row["role"]

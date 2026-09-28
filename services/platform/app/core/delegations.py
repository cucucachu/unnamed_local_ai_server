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

from app.core import sessions
from app.core.errors import InvalidInput, Unauthorized
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

"""Routine grants: how a scheduled run acts as its user (docs/PLATFORM.md §4 "Routine grant").

When a logged-in user saves or enables a routine, agent-server trades their
identity token for a grant bound to (user, routine, space). The grant is a
`sessions` row with `routine_id`/`routine_space_id` set; agent-server keeps
its `hr_` secret, only the SHA-256 is stored here. At each run agent-server
exchanges the grant for an ordinary delegation (`act=agent`,
`thr=<run thread>`) and refreshes that the usual way.

Because it is a session, every session check applies: the grant dies with a
password change, a disable, sign-out-everywhere, or a revoke from Settings ->
Sessions; and each delegated request re-checks it (`sessions.load_active`),
which for a grant also means edit rights on its active space. A grant that
fails that check at exchange is revoked for good, so getting edit rights
back later doesn't revive it. Its delegations reach only its space
(`Principal.space_scope`). It never authenticates a person:
`sessions.resolve_token` skips these rows.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from uuid import UUID

from psycopg import AsyncConnection

from app.core import delegations, sessions
from app.core.errors import Forbidden, InvalidInput, Unauthorized
from app.core.tokens import TokenError, TokenService

GRANT_PREFIX = "hr_"
# Lives until revoked; the expiry only bounds a grant agent-server lost track of.
GRANT_TTL = timedelta(days=3650)
EDIT_ROLES = frozenset({"owner", "editor"})
_ROUTINE_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


async def issue(
    conn: AsyncConnection,
    tokens: TokenService,
    identity_token: str,
    routine_id: str,
    space_path: str,
    label: str | None,
) -> tuple[str, UUID, str]:
    """`(grant, grant id, canonical space)`; replaces any earlier grant for the routine."""
    if not _ROUTINE_ID.fullmatch(routine_id):
        raise InvalidInput("invalid_routine_id")
    try:
        claims = tokens.verify_token(identity_token, act="user")
        user_id, session_id = UUID(str(claims["sub"])), UUID(str(claims.get("sid")))
    except (TokenError, ValueError) as exc:
        raise Unauthorized("unauthenticated") from exc
    if await sessions.load_active(conn, session_id, user_id) is None:
        raise Unauthorized("unauthenticated")
    space, role = await delegations.member_space(conn, user_id, space_path)
    if role not in EDIT_ROLES:
        raise Forbidden("insufficient_role")

    grant = sessions.new_token(GRANT_PREFIX)
    async with conn.transaction():
        await _revoke_routine(conn, user_id, routine_id)
        cur = await conn.execute(
            "INSERT INTO sessions "
            "(token_hash, user_id, device_label, routine_id, routine_space_id, expires_at) "
            "VALUES (%s, %s, %s, %s, %s, now() + %s) RETURNING id",
            (
                sessions.hash_token(grant),
                user_id,
                sessions.clean_device_label(label),
                routine_id,
                space["id"],
                GRANT_TTL,
            ),
        )
        grant_id = (await cur.fetchone())["id"]
    return grant, grant_id, delegations.canonical_space(space)


async def exchange(
    conn: AsyncConnection, tokens: TokenService, grant: str, routine_id: str, thread_id: str
) -> tuple[str, datetime]:
    """A delegation for one run thread of the routine the grant was issued for."""
    row = await _find(conn, grant)
    if row is None or row["routine_id"] != routine_id:
        raise Unauthorized("unauthenticated")
    try:
        return await delegations.issue(conn, tokens, row["user_id"], row["id"], thread_id)
    except Unauthorized:
        await sessions.revoke_session(conn, row["id"], row["user_id"])
        raise


async def revoke(conn: AsyncConnection, grant: str) -> None:
    """Idempotent: the routine was deleted or disabled."""
    row = await _find(conn, grant)
    if row is not None:
        await sessions.revoke_session(conn, row["id"], row["user_id"])


async def _find(conn: AsyncConnection, grant: str) -> sessions.Row | None:
    if not grant.startswith(GRANT_PREFIX):
        return None
    cur = await conn.execute(
        "SELECT id, user_id, routine_id FROM sessions "
        "WHERE token_hash = %s AND routine_id IS NOT NULL "
        "AND revoked_at IS NULL AND expires_at > now()",
        (sessions.hash_token(grant),),
    )
    return await cur.fetchone()


async def _revoke_routine(conn: AsyncConnection, user_id: UUID, routine_id: str) -> None:
    await conn.execute(
        "UPDATE sessions SET revoked_at = now() "
        "WHERE user_id = %s AND routine_id = %s AND revoked_at IS NULL",
        (user_id, routine_id),
    )

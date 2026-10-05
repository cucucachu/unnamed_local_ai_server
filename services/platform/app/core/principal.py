"""Who is calling `/api/platform/*` (docs/PLATFORM.md §4).

Two credentials are accepted, in this order:

1. `X-HomeAI-Identity: <JWT act=user>` - set by Caddy's `forward_auth` from
   `/internal/auth/verify` (Caddy strips any client-supplied copy). If the
   header is present it decides the outcome; a bad one is never retried
   against `Authorization`.
2. `Authorization: Bearer <JWT act=agent>` - a delegation token from an
   internal service (M11-02). Caddy may pass a native client's own
   `Authorization: Bearer hs_...` through as well; opaque session tokens are
   only valid at `/internal/auth/verify`, never here.

Either way the session the token names must still be active and its user
enabled, and role/step-up come from the database, not the token. A
delegation minted from a routine grant is also confined to the grant's space
(`Principal.space_scope`).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Annotated
from uuid import UUID

from fastapi import Depends
from psycopg import AsyncConnection
from starlette.requests import HTTPConnection

from app.core import sessions
from app.core.errors import Forbidden, Unauthorized
from app.core.tokens import TokenError, TokenService

IDENTITY_HEADER = "X-HomeAI-Identity"
IDENTITY_TTL = timedelta(minutes=5)


@dataclass(frozen=True)
class Principal:
    user_id: UUID
    session_id: UUID
    username: str
    display_name: str
    role: str
    act: str
    stepped_up: bool
    thread_id: str | None = None
    # Numeric owner of files created on the user's behalf.
    uid: int | None = None
    # A routine run's delegation reaches only its routine's space.
    space_scope: UUID | None = None

    @property
    def is_agent(self) -> bool:
        return self.act != "user"


def issue_identity_token(tokens: TokenService, *, user_id: UUID, session_id: UUID, role: str):
    claims = {"sub": str(user_id), "sid": str(session_id), "role": role, "act": "user"}
    return tokens.issue_token(claims, IDENTITY_TTL)


def bearer_token(request: HTTPConnection) -> str | None:
    scheme, _, credential = request.headers.get("authorization", "").partition(" ")
    credential = credential.strip()
    if scheme.lower() != "bearer" or not credential:
        return None
    return credential


def _claims(request: HTTPConnection) -> dict:
    tokens: TokenService = request.app.state.tokens
    identity = request.headers.get(IDENTITY_HEADER)
    try:
        if identity is not None:
            return tokens.verify_token(identity, act="user")
        bearer = bearer_token(request)
        if bearer is not None and not bearer.startswith(sessions.TOKEN_PREFIX):
            return tokens.verify_token(bearer, act="agent")
    except TokenError as exc:
        raise Unauthorized("unauthenticated") from exc
    raise Unauthorized("unauthenticated")


async def require_user(request: HTTPConnection) -> Principal:
    """Any authenticated principal: a user via Caddy, or their agent via delegation.

    Also called directly on a WebSocket's handshake (`/ws/platform/*`).
    """
    async with request.app.state.db_pool.connection() as conn:
        return await _principal(conn, _claims(request))


async def from_delegation(conn: AsyncConnection, tokens: TokenService, token: str) -> Principal:
    """The agent principal a delegation token names (for `/internal/*` routes that take one)."""
    try:
        claims = tokens.verify_token(token, act="agent")
    except TokenError as exc:
        raise Unauthorized("unauthenticated") from exc
    return await _principal(conn, claims)


async def _principal(conn: AsyncConnection, claims: dict) -> Principal:
    try:
        user_id, session_id = UUID(claims["sub"]), UUID(str(claims.get("sid")))
    except ValueError as exc:
        raise Unauthorized("unauthenticated") from exc
    row = await sessions.load_active(conn, session_id, user_id)
    if row is None:
        raise Unauthorized("unauthenticated")
    thread_id = claims.get("thr")
    return Principal(
        user_id=user_id,
        session_id=session_id,
        username=row["username"],
        display_name=row["display_name"],
        role=row["role"],
        act=claims["act"],
        stepped_up=row["stepped_up"],
        thread_id=thread_id if isinstance(thread_id, str) else None,
        uid=row["uid"],
        space_scope=row["routine_space_id"],
    )


async def require_human(principal: Annotated[Principal, Depends(require_user)]) -> Principal:
    """The user themselves (`act=user`); agents never manage auth or sessions."""
    if principal.is_agent:
        raise Forbidden("agent_not_allowed")
    return principal


async def require_admin_stepped_up(
    principal: Annotated[Principal, Depends(require_user)],
) -> Principal:
    """An admin, acting in person, inside a fresh step-up window."""
    if principal.is_agent:
        raise Forbidden("agent_not_allowed")
    if principal.role != "admin":
        raise Forbidden("admin_required")
    if not principal.stepped_up:
        raise Forbidden("step_up_required")
    return principal


CurrentUser = Annotated[Principal, Depends(require_user)]
HumanUser = Annotated[Principal, Depends(require_human)]
SteppedUpAdmin = Annotated[Principal, Depends(require_admin_stepped_up)]

"""Request/response plumbing for session credentials, shared by the auth routes and verify."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from fastapi import Request, Response

from app.api.schemas import SessionResponse, UserOut
from app.core import sessions
from app.core.errors import Forbidden, Unauthorized
from app.core.principal import bearer_token
from app.core.ratelimit import RateLimiter

SESSION_COOKIE = "homeai_session"
CLIENT_HEADER = "X-HomeAI-Client"


def session_token(request: Request) -> str | None:
    """A native client's `Authorization: Bearer hs_...`, else the web cookie."""
    bearer = bearer_token(request)
    if bearer is not None and bearer.startswith(sessions.TOKEN_PREFIX):
        return bearer
    return request.cookies.get(SESSION_COOKIE)


def is_native(request: Request) -> bool:
    return request.headers.get(CLIENT_HEADER, "").strip().lower() == "native"


def is_https(request: Request) -> bool:
    proto = request.headers.get("x-forwarded-proto")
    if proto:
        return proto.split(",")[0].strip().lower() == "https"
    return request.url.scheme == "https"


def client_ip(request: Request) -> str:
    """The address Caddy appended to `X-Forwarded-For` (the last hop), else the peer.

    Earlier entries are client-supplied and trivially spoofed.
    """
    forwarded = [part.strip() for part in request.headers.get("x-forwarded-for", "").split(",")]
    if forwarded and forwarded[-1]:
        return forwarded[-1]
    return request.client.host if request.client else "unknown"


def set_session_cookie(request: Request, response: Response, token: str) -> None:
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=int(sessions.SESSION_TTL.total_seconds()),
        path="/",
        httponly=True,
        samesite="lax",
        secure=is_https(request),
    )


def clear_session_cookie(request: Request, response: Response) -> None:
    response.delete_cookie(
        SESSION_COOKIE, path="/", httponly=True, samesite="lax", secure=is_https(request)
    )


def session_response(
    request: Request, response: Response, user: dict[str, Any], token: str
) -> SessionResponse:
    """Web clients get the cookie; native clients get the token in the body instead."""
    if is_native(request):
        return SessionResponse(user=UserOut.model_validate(user), session_token=token)
    set_session_cookie(request, response, token)
    return SessionResponse(user=UserOut.model_validate(user))


@contextmanager
def credential_attempt(request: Request, *keys: str) -> Iterator[None]:
    """Rate-limit a credential check against `keys` (raises `RateLimited` when full).

    Only failed credentials (401/403) keep their slot; success and input
    errors (422/409) give it back.
    """
    limiter: RateLimiter = request.app.state.limiter
    taken = limiter.acquire(keys)
    try:
        yield
    except (Unauthorized, Forbidden):
        raise
    except BaseException:
        limiter.release(taken)
        raise
    limiter.release(taken)

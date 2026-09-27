"""`/api/auth/*`: unauthenticated at Caddy; these routes read the session credential themselves.

Every credential check (setup code, password, invite token) is rate-limited
via `credential_attempt`. Contract: docs/ARCHITECTURE.md §3 "Platform API".
"""

import asyncio

from fastapi import APIRouter, Request, Response, status

from app.api.schemas import (
    InviteAcceptRequest,
    LoginRequest,
    SessionResponse,
    SetupRequest,
    StatusResponse,
    StepUpRequest,
    StepUpResponse,
    UserOut,
)
from app.api.session_http import (
    SESSION_COOKIE,
    clear_session_cookie,
    client_ip,
    credential_attempt,
    session_response,
    session_token,
    set_session_cookie,
)
from app.core import invites, passwords, sessions, totp, users
from app.core.bootstrap import Bootstrap
from app.core.errors import Forbidden, Unauthorized

router = APIRouter(prefix="/api/auth")


async def _current_session(request: Request):
    token = session_token(request)
    if token is None:
        return None, None
    async with request.app.state.db_pool.connection() as conn:
        return token, await sessions.resolve_token(conn, token)


@router.get("/status", response_model=StatusResponse, response_model_exclude_none=True)
async def auth_status(request: Request, response: Response) -> StatusResponse:
    """Also re-issues the web cookie, so its Max-Age follows the sliding session expiry."""
    bootstrap: Bootstrap = request.app.state.bootstrap
    token, row = await _current_session(request)
    if row is None:
        return StatusResponse(setup_required=bootstrap.setup_required, authenticated=False)
    if token == request.cookies.get(SESSION_COOKIE):
        set_session_cookie(request, response, token)
    return StatusResponse(
        setup_required=bootstrap.setup_required,
        authenticated=True,
        user=UserOut.model_validate(row),
    )


@router.post("/setup", response_model=SessionResponse, response_model_exclude_none=True)
async def setup(body: SetupRequest, request: Request, response: Response) -> SessionResponse:
    bootstrap: Bootstrap = request.app.state.bootstrap
    with credential_attempt(request, f"setup-ip:{client_ip(request)}"):
        async with request.app.state.db_pool.connection() as conn:
            user, token = await bootstrap.complete(
                conn,
                setup_code=body.setup_code,
                username=body.username,
                display_name=body.display_name,
                password=body.password,
                device_label=body.device_label,
            )
    return session_response(request, response, user, token)


@router.post("/login", response_model=SessionResponse, response_model_exclude_none=True)
async def login(body: LoginRequest, request: Request, response: Response) -> SessionResponse:
    """Checks run password, then TOTP, then disabled, so a wrong password never
    reveals whether the account has TOTP or is disabled."""
    username = body.username.strip().lower()
    ip = client_ip(request)
    with credential_attempt(request, f"login-user:{username}", f"login-ip:{ip}"):
        async with request.app.state.db_pool.connection() as conn:
            creds = await users.get_credentials(conn, username)
            if creds is None:
                await asyncio.to_thread(passwords.burn_verify, body.password)
                raise Unauthorized("invalid_credentials")
            if not await asyncio.to_thread(
                passwords.verify_password, creds["password_hash"], body.password
            ):
                raise Unauthorized("invalid_credentials")

            if creds["totp_secret"] is not None:
                if not body.totp_code:
                    raise Unauthorized("totp_required")
                step = totp.match_step(
                    creds["totp_secret"], body.totp_code, after_step=creds["totp_last_step"]
                )
                if step is None or not await users.consume_totp_step(conn, creds["id"], step):
                    raise Unauthorized("invalid_totp")

            if creds["disabled_at"] is not None:
                raise Forbidden("account_disabled")

            await users.rehash_if_needed(conn, creds["id"], body.password)
            token, _ = await sessions.create_session(conn, creds["id"], body.device_label)
            user = await users.get_user(conn, creds["id"])
    return session_response(request, response, user, token)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(request: Request, response: Response) -> None:
    """Idempotent: always 204 and clears the cookie, even with no valid session."""
    _, row = await _current_session(request)
    if row is not None:
        async with request.app.state.db_pool.connection() as conn:
            await sessions.revoke_session(conn, row["session_id"], row["id"])
    clear_session_cookie(request, response)


@router.post("/step-up", response_model=StepUpResponse)
async def step_up(body: StepUpRequest, request: Request) -> StepUpResponse:
    _, row = await _current_session(request)
    if row is None:
        raise Unauthorized("unauthenticated")
    ip = client_ip(request)
    with credential_attempt(request, f"reauth-user:{row['id']}", f"reauth-ip:{ip}"):
        async with request.app.state.db_pool.connection() as conn:
            if not await users.check_password(conn, row["id"], body.password):
                raise Forbidden("invalid_password")
            until = await sessions.step_up(conn, row["session_id"])
    return StepUpResponse(stepped_up_until=until)


@router.post("/invite/accept", response_model=SessionResponse, response_model_exclude_none=True)
async def accept_invite(
    body: InviteAcceptRequest, request: Request, response: Response
) -> SessionResponse:
    with credential_attempt(request, f"accept-ip:{client_ip(request)}"):
        async with request.app.state.db_pool.connection() as conn:
            user, token = await invites.accept_invite(
                conn,
                body.token.strip(),
                username=body.username,
                display_name=body.display_name,
                password=body.password,
                device_label=body.device_label,
            )
    return session_response(request, response, user, token)

"""`/api/auth/*`: unauthenticated at Caddy; these routes read the session credential themselves.

Every credential check (setup code, password, invite token) is rate-limited
via `credential_attempt`. Contract: docs/ARCHITECTURE.md §3 "Platform API".
"""

import asyncio

from fastapi import APIRouter, Request, Response, status

from app.api.schemas import (
    InviteAcceptRequest,
    LoginRequest,
    PasskeyCredentialRequest,
    PasskeyLoginBeginRequest,
    PasskeyLoginFinishRequest,
    SessionResponse,
    SetupRequest,
    StatusResponse,
    StepUpRequest,
    StepUpResponse,
    UserOut,
    WebAuthnStatus,
)
from app.api.session_http import (
    SESSION_COOKIE,
    clear_session_cookie,
    client_ip,
    credential_attempt,
    is_native,
    session_response,
    session_token,
    set_session_cookie,
)
from app.core import invites, legacy, passwords, sessions, totp, users, webauthn, wireguard
from app.core.bootstrap import Bootstrap
from app.core.errors import Forbidden, InvalidInput, Unauthorized
from app.core.origin import require_privileged_origin

router = APIRouter(prefix="/api/auth")


async def _current_session(request: Request):
    token = session_token(request)
    if token is None:
        return None, None
    async with request.app.state.db_pool.connection() as conn:
        return token, await sessions.resolve_token(conn, token)


def _webauthn_status(request: Request) -> WebAuthnStatus:
    settings = request.app.state.settings
    rid = webauthn.rp_id(settings)
    return WebAuthnStatus(rp_id=rid, origin_ok=webauthn.origin_ok(request, settings))


def _passkeys_required(request: Request, require_passkeys: bool) -> bool:
    """Browser (not native) must use a passkey when the admin flagged the user
    and an RP ID is configured. Native stays on password until M15-06."""
    return (
        require_passkeys
        and webauthn.rp_id(request.app.state.settings) is not None
        and not is_native(request)
    )


async def _finish_login(
    request: Request,
    response: Response,
    creds: dict,
    *,
    totp_code: str | None,
    device_label: str | None,
    device_id,
) -> SessionResponse:
    """Shared password/passkey tail: TOTP, disabled, then mint a session."""
    async with request.app.state.db_pool.connection() as conn:
        if creds["totp_secret"] is not None:
            if not totp_code:
                raise Unauthorized("totp_required")
            step = totp.match_step(
                creds["totp_secret"], totp_code, after_step=creds["totp_last_step"]
            )
            if step is None or not await users.consume_totp_step(conn, creds["id"], step):
                raise Unauthorized("invalid_totp")

        if creds["disabled_at"] is not None:
            raise Forbidden("account_disabled")

        bound_device = None
        if device_id is not None:
            wg: wireguard.WireGuardService = request.app.state.wireguard
            if not await wg.peer_belongs_to_user(conn, device_id, creds["id"]):
                raise InvalidInput("unknown_device")
            bound_device = device_id
        token, _ = await sessions.create_session(
            conn, creds["id"], device_label, device_id=bound_device
        )
        user = await users.get_user(conn, creds["id"])
    return session_response(request, response, user, token)


@router.get("/status", response_model=StatusResponse, response_model_exclude_none=True)
async def auth_status(request: Request, response: Response) -> StatusResponse:
    """Also re-issues the web cookie, so its Max-Age follows the sliding session expiry."""
    bootstrap: Bootstrap = request.app.state.bootstrap
    token, row = await _current_session(request)
    webauthn_out = _webauthn_status(request)
    if row is None:
        return StatusResponse(
            setup_required=bootstrap.setup_required, authenticated=False, webauthn=webauthn_out
        )
    if token == request.cookies.get(SESSION_COOKIE):
        set_session_cookie(request, response, token)
    return StatusResponse(
        setup_required=bootstrap.setup_required,
        authenticated=True,
        user=UserOut.model_validate(row),
        webauthn=webauthn_out,
    )


@router.post("/setup", response_model=SessionResponse, response_model_exclude_none=True)
async def setup(body: SetupRequest, request: Request, response: Response) -> SessionResponse:
    require_privileged_origin(request)
    bootstrap: Bootstrap = request.app.state.bootstrap
    with credential_attempt(request, f"setup-ip:{client_ip(request)}"):
        async with request.app.state.db_pool.connection() as conn:
            user, token = await bootstrap.complete(
                conn,
                setup_code=body.setup_code,
                username=body.username,
                display_name=body.display_name,
                password=body.password,
                storage=request.app.state.storage,
                device_label=body.device_label,
            )
    await legacy.maybe_migrate(request.app)
    return session_response(request, response, user, token)


@router.post("/login", response_model=SessionResponse, response_model_exclude_none=True)
async def login(body: LoginRequest, request: Request, response: Response) -> SessionResponse:
    """Checks run password, then TOTP, then disabled, so a wrong password never
    reveals whether the account has TOTP or is disabled.

    When `require_passkeys` is set and an RP ID is configured, browsers
    (`403 passkey_required`) must use the passkey login path; native is
    exempt (M15-06). The password is not checked in that case.
    """
    username = body.username.strip().lower()
    ip = client_ip(request)
    with credential_attempt(request, f"login-user:{username}", f"login-ip:{ip}"):
        async with request.app.state.db_pool.connection() as conn:
            creds = await users.get_credentials(conn, username)
            if creds is None:
                await asyncio.to_thread(passwords.burn_verify, body.password)
                raise Unauthorized("invalid_credentials")
            if _passkeys_required(request, creds["require_passkeys"]):
                raise Forbidden("passkey_required")
            if not await asyncio.to_thread(
                passwords.verify_password, creds["password_hash"], body.password
            ):
                raise Unauthorized("invalid_credentials")
            await users.rehash_if_needed(conn, creds["id"], body.password)
        return await _finish_login(
            request,
            response,
            creds,
            totp_code=body.totp_code,
            device_label=body.device_label,
            device_id=body.device_id,
        )


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
        if _passkeys_required(request, row["require_passkeys"]):
            raise Forbidden("passkey_required")
        async with request.app.state.db_pool.connection() as conn:
            if not await users.check_password(conn, row["id"], body.password):
                raise Forbidden("invalid_password")
            until = await sessions.step_up(conn, row["session_id"])
    return StepUpResponse(stepped_up_until=until)


@router.post("/passkey/login/begin")
async def passkey_login_begin(body: PasskeyLoginBeginRequest, request: Request) -> dict:
    username = body.username.strip().lower()
    rid = webauthn.require_rp_id(request.app.state.settings)
    webauthn.require_matching_origin(request, rid)
    ip = client_ip(request)
    with credential_attempt(request, f"login-user:{username}", f"login-ip:{ip}"):
        async with request.app.state.db_pool.connection() as conn:
            creds = await users.get_credentials(conn, username)
            if creds is None:
                raise Unauthorized("invalid_credentials")
            return await webauthn.begin_login(conn, creds["id"], rid)


@router.post(
    "/passkey/login/finish", response_model=SessionResponse, response_model_exclude_none=True
)
async def passkey_login_finish(
    body: PasskeyLoginFinishRequest, request: Request, response: Response
) -> SessionResponse:
    username = body.username.strip().lower()
    rid = webauthn.require_rp_id(request.app.state.settings)
    origin = webauthn.require_matching_origin(request, rid)
    ip = client_ip(request)
    with credential_attempt(request, f"login-user:{username}", f"login-ip:{ip}"):
        async with request.app.state.db_pool.connection() as conn:
            creds = await users.get_credentials(conn, username)
            if creds is None:
                raise Unauthorized("invalid_credentials")
            await webauthn.finish_assertion(
                conn,
                purpose="login",
                expected_user_id=creds["id"],
                rid=rid,
                origin=origin,
                credential=body.credential,
            )
        return await _finish_login(
            request,
            response,
            creds,
            totp_code=body.totp_code,
            device_label=body.device_label,
            device_id=body.device_id,
        )


@router.post("/passkey/step-up/begin")
async def passkey_step_up_begin(request: Request) -> dict:
    _, row = await _current_session(request)
    if row is None:
        raise Unauthorized("unauthenticated")
    rid = webauthn.require_rp_id(request.app.state.settings)
    webauthn.require_matching_origin(request, rid)
    ip = client_ip(request)
    with credential_attempt(request, f"reauth-user:{row['id']}", f"reauth-ip:{ip}"):
        async with request.app.state.db_pool.connection() as conn:
            return await webauthn.begin_step_up(conn, row["id"], rid)


@router.post("/passkey/step-up/finish", response_model=StepUpResponse)
async def passkey_step_up_finish(
    body: PasskeyCredentialRequest, request: Request
) -> StepUpResponse:
    _, row = await _current_session(request)
    if row is None:
        raise Unauthorized("unauthenticated")
    rid = webauthn.require_rp_id(request.app.state.settings)
    origin = webauthn.require_matching_origin(request, rid)
    ip = client_ip(request)
    with credential_attempt(request, f"reauth-user:{row['id']}", f"reauth-ip:{ip}"):
        async with request.app.state.db_pool.connection() as conn:
            await webauthn.finish_assertion(
                conn,
                purpose="step_up",
                expected_user_id=row["id"],
                rid=rid,
                origin=origin,
                credential=body.credential,
            )
            until = await sessions.step_up(conn, row["session_id"])
    return StepUpResponse(stepped_up_until=until)


@router.post("/invite/accept", response_model=SessionResponse, response_model_exclude_none=True)
async def accept_invite(
    body: InviteAcceptRequest, request: Request, response: Response
) -> SessionResponse:
    require_privileged_origin(request)
    with credential_attempt(request, f"accept-ip:{client_ip(request)}"):
        async with request.app.state.db_pool.connection() as conn:
            user, token = await invites.accept_invite(
                conn,
                body.token.strip(),
                username=body.username,
                display_name=body.display_name,
                password=body.password,
                storage=request.app.state.storage,
                device_label=body.device_label,
            )
    return session_response(request, response, user, token)

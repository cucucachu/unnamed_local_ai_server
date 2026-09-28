"""`/api/platform/me*`: the caller's own profile, sessions, and TOTP."""

from uuid import UUID

from fastapi import APIRouter, Request, status

from app.api.schemas import (
    MePatchRequest,
    PasskeyList,
    PasskeyOut,
    PasskeyRegisterFinishRequest,
    PasswordRequest,
    SessionList,
    SessionOut,
    TotpConfirmRequest,
    TotpEnrollResponse,
    UserOut,
)
from app.api.session_http import client_ip, credential_attempt
from app.core import passwords, sessions, totp, users, webauthn
from app.core.errors import Conflict, Forbidden, InvalidInput, NotFound
from app.core.origin import require_privileged_origin
from app.core.principal import CurrentUser, HumanUser, Principal

router = APIRouter(prefix="/me")


async def _check_current_password(request: Request, principal: Principal, password: str):
    ip = client_ip(request)
    with credential_attempt(request, f"reauth-user:{principal.user_id}", f"reauth-ip:{ip}"):
        async with request.app.state.db_pool.connection() as conn:
            if not await users.check_password(conn, principal.user_id, password):
                raise Forbidden("invalid_password")


@router.get("", response_model=UserOut)
async def get_me(request: Request, principal: CurrentUser):
    async with request.app.state.db_pool.connection() as conn:
        return await users.get_user(conn, principal.user_id)


@router.patch("", response_model=UserOut)
async def patch_me(body: MePatchRequest, request: Request, principal: HumanUser):
    """A password change revokes every other session of the user."""
    if body.password is not None:
        if not body.current_password:
            raise InvalidInput("current_password_required")
        passwords.check_password_policy(body.password)
        await _check_current_password(request, principal, body.current_password)
    async with request.app.state.db_pool.connection() as conn:
        if body.display_name is not None:
            await users.set_display_name(conn, principal.user_id, body.display_name)
        if body.password is not None:
            await users.set_password(
                conn, principal.user_id, body.password, keep_session_id=principal.session_id
            )
        return await users.get_user(conn, principal.user_id)


@router.get("/passkeys", response_model=PasskeyList)
async def list_passkeys(request: Request, principal: HumanUser):
    """Public origin allowed (same as WireGuard revoke)."""
    webauthn.require_rp_id(request.app.state.settings)
    async with request.app.state.db_pool.connection() as conn:
        rows = await webauthn.list_credentials(conn, principal.user_id)
    return PasskeyList(passkeys=[PasskeyOut(**row) for row in rows])


@router.post("/passkeys/register/begin")
async def passkey_register_begin(request: Request, principal: HumanUser) -> dict:
    require_privileged_origin(request)
    rid = webauthn.require_rp_id(request.app.state.settings)
    webauthn.require_matching_origin(request, rid)
    ip = client_ip(request)
    with credential_attempt(request, f"reauth-user:{principal.user_id}", f"reauth-ip:{ip}"):
        async with request.app.state.db_pool.connection() as conn:
            return await webauthn.begin_register(conn, principal, rid)


@router.post("/passkeys/register/finish", response_model=PasskeyOut)
async def passkey_register_finish(
    body: PasskeyRegisterFinishRequest, request: Request, principal: HumanUser
):
    require_privileged_origin(request)
    rid = webauthn.require_rp_id(request.app.state.settings)
    origin = webauthn.require_matching_origin(request, rid)
    ip = client_ip(request)
    with credential_attempt(request, f"reauth-user:{principal.user_id}", f"reauth-ip:{ip}"):
        async with request.app.state.db_pool.connection() as conn:
            return await webauthn.finish_register(
                conn, principal, rid, origin, body.credential, body.name
            )


@router.delete("/passkeys/{credential_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_passkey(credential_id: UUID, request: Request, principal: HumanUser) -> None:
    webauthn.require_rp_id(request.app.state.settings)
    async with request.app.state.db_pool.connection() as conn:
        if not await webauthn.delete_credential(conn, principal.user_id, credential_id):
            raise NotFound("not_found")


@router.get("/sessions", response_model=SessionList)
async def list_my_sessions(request: Request, principal: HumanUser):
    async with request.app.state.db_pool.connection() as conn:
        rows = await sessions.list_user_sessions(conn, principal.user_id)
    return SessionList(
        sessions=[SessionOut(**row, current=row["id"] == principal.session_id) for row in rows]
    )


@router.delete("/sessions/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_my_session(session_id: UUID, request: Request, principal: HumanUser) -> None:
    async with request.app.state.db_pool.connection() as conn:
        if not await sessions.revoke_session(conn, session_id, principal.user_id):
            raise NotFound("not_found")


@router.post("/totp/enroll", response_model=TotpEnrollResponse)
async def enroll_totp(body: PasswordRequest, request: Request, principal: HumanUser):
    """Starts (or restarts) enrollment; nothing changes at login until `confirm`."""
    await _check_current_password(request, principal, body.password)
    async with request.app.state.db_pool.connection() as conn:
        state = await users.get_totp_state(conn, principal.user_id)
        if state["totp_secret"] is not None:
            raise Conflict("totp_already_enabled")
        secret = totp.generate_secret()
        await users.set_pending_totp(conn, principal.user_id, secret)
    return TotpEnrollResponse(
        secret=secret, otpauth_uri=totp.provisioning_uri(secret, state["username"])
    )


@router.post("/totp/confirm", response_model=UserOut)
async def confirm_totp(body: TotpConfirmRequest, request: Request, principal: HumanUser):
    ip = client_ip(request)
    with credential_attempt(request, f"reauth-user:{principal.user_id}", f"reauth-ip:{ip}"):
        async with request.app.state.db_pool.connection() as conn:
            state = await users.get_totp_state(conn, principal.user_id)
            if state["totp_secret"] is not None:
                raise Conflict("totp_already_enabled")
            if state["totp_pending_secret"] is None:
                raise Conflict("totp_not_pending")
            step = totp.match_step(state["totp_pending_secret"], body.code)
            if step is None:
                raise Forbidden("invalid_totp")
            await users.activate_pending_totp(conn, principal.user_id, step)
            return await users.get_user(conn, principal.user_id)


@router.post("/totp/disable", response_model=UserOut)
async def disable_totp(body: PasswordRequest, request: Request, principal: HumanUser):
    await _check_current_password(request, principal, body.password)
    async with request.app.state.db_pool.connection() as conn:
        state = await users.get_totp_state(conn, principal.user_id)
        if state["totp_secret"] is None:
            raise Conflict("totp_not_enabled")
        await users.clear_totp(conn, principal.user_id)
        return await users.get_user(conn, principal.user_id)

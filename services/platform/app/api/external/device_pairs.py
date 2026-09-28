"""`/api/platform/me/device-pairs*`: this user's host-app pairings (M15-06).

Human-only (`act=user`); agents get `403 agent_not_allowed`. Begin enroll
is LAN/VPN-only (`403 public_origin`); list and revoke stay allowed from a
public origin so a stolen phone can be revoked off-LAN (like WireGuard).
Members pair their own device; not admin-only.
"""

from uuid import UUID

from fastapi import APIRouter, Request, status

from app.api.schemas import DevicePairBeginResponse, DevicePairList, DevicePairOut
from app.api.session_http import client_ip, credential_attempt
from app.core import device_pairs
from app.core.errors import NotFound
from app.core.origin import require_privileged_origin
from app.core.principal import HumanUser

router = APIRouter(prefix="/me/device-pairs")


@router.get("", response_model=DevicePairList)
async def list_pairs(request: Request, principal: HumanUser):
    async with request.app.state.db_pool.connection() as conn:
        rows = await device_pairs.list_pairs(conn, principal.user_id)
    return DevicePairList(devices=[DevicePairOut(**row) for row in rows])


@router.post("/begin", response_model=DevicePairBeginResponse)
async def begin_pair(request: Request, principal: HumanUser) -> DevicePairBeginResponse:
    """Short-lived enroll token + challenge; Settings shows this as a LAN QR."""
    require_privileged_origin(request)
    ip = client_ip(request)
    with credential_attempt(request, f"reauth-user:{principal.user_id}", f"reauth-ip:{ip}"):
        async with request.app.state.db_pool.connection() as conn:
            payload = await device_pairs.begin_enroll(conn, principal.user_id, principal.username)
    return DevicePairBeginResponse(**payload)


@router.delete("/{pair_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_pair(pair_id: UUID, request: Request, principal: HumanUser) -> None:
    async with request.app.state.db_pool.connection() as conn:
        if not await device_pairs.revoke_pair(conn, principal.user_id, pair_id):
            raise NotFound("not_found")

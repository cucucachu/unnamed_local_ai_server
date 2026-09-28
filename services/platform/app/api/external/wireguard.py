"""`/api/platform/me/wireguard-devices*`: this user's WireGuard peers (M15-01).

Human-only (`act=user`); agents get `403 agent_not_allowed`. Create is
allowed from the LAN for now — origin policy is M15-02. Admin does not
manage other users' peers in v1.
"""

from uuid import UUID

from fastapi import APIRouter, Request, status

from app.api.schemas import (
    WireGuardDeviceCreated,
    WireGuardDeviceCreateRequest,
    WireGuardDeviceList,
    WireGuardDeviceOut,
)
from app.core.errors import NotFound
from app.core.principal import HumanUser
from app.core.wireguard import WireGuardService

router = APIRouter(prefix="/me/wireguard-devices")


def _wg(request: Request) -> WireGuardService:
    return request.app.state.wireguard


@router.get("", response_model=WireGuardDeviceList)
async def list_devices(request: Request, principal: HumanUser):
    async with request.app.state.db_pool.connection() as conn:
        rows = await _wg(request).list_peers(conn, principal.user_id)
    return WireGuardDeviceList(devices=[WireGuardDeviceOut(**row) for row in rows])


@router.post("", response_model=WireGuardDeviceCreated, status_code=status.HTTP_201_CREATED)
async def create_device(
    body: WireGuardDeviceCreateRequest, request: Request, principal: HumanUser
):
    """The wg-quick config (with the peer private key) is in `config` this once."""
    async with request.app.state.db_pool.connection() as conn:
        row, config = await _wg(request).create_peer(conn, principal.user_id, body.name)
    return WireGuardDeviceCreated(**row, config=config)


@router.delete("/{device_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_device(device_id: UUID, request: Request, principal: HumanUser) -> None:
    async with request.app.state.db_pool.connection() as conn:
        if not await _wg(request).revoke_peer(conn, principal.user_id, device_id):
            raise NotFound("not_found")

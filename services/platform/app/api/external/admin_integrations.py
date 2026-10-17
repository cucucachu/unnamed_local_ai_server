"""`/api/platform/admin/integrations`: install and configure integrations (M18-03).

Admin, in person, stepped up, from a privileged origin, like the rest of
`/admin`. No response carries a credential: connections are listed by
status only.
"""

from uuid import UUID

from fastapi import APIRouter, Depends, Request, status
from pydantic import ValidationError

from app.api.external import integration_oauth as oauth_pages
from app.api.external.admin import require_admin_from_privileged_origin
from app.api.schemas import (
    AdminIntegrationList,
    AdminIntegrationOut,
    AdminIntegrationToolChanges,
    ConnectionOut,
    CredentialIn,
    DiscoveryIn,
    IntegrationCheckOut,
    IntegrationInstall,
    IntegrationPatch,
    OAuthStartOut,
)
from app.core import integration_oauth, integrations
from app.core.errors import InvalidInput, Unavailable
from app.core.principal import SteppedUpAdmin

router = APIRouter(
    prefix="/admin/integrations", dependencies=[Depends(require_admin_from_privileged_origin)]
)


async def _one(conn, integration_id: UUID) -> AdminIntegrationOut:
    for row in await integrations.admin_view(conn):
        if row["id"] == integration_id:
            return AdminIntegrationOut.model_validate(row)
    await integrations.get(conn, integration_id)  # 404
    raise AssertionError("unreachable")


@router.get("", response_model=AdminIntegrationList)
async def list_all(request: Request) -> AdminIntegrationList:
    async with request.app.state.db_pool.connection() as conn:
        return AdminIntegrationList(integrations=await integrations.admin_view(conn))


@router.post("", response_model=AdminIntegrationOut, status_code=status.HTTP_201_CREATED)
async def install(
    body: IntegrationInstall, request: Request, principal: SteppedUpAdmin
) -> AdminIntegrationOut:
    """Register a server. Nothing is discovered yet: Refresh does that, through the gateway."""
    async with request.app.state.db_pool.connection() as conn:
        integration_id = await integrations.install(conn, principal, body.model_dump())
        return await _one(conn, integration_id)


@router.patch("/{integration_id}", response_model=AdminIntegrationOut)
async def edit(
    integration_id: UUID, body: IntegrationPatch, request: Request, principal: SteppedUpAdmin
) -> AdminIntegrationOut:
    async with request.app.state.db_pool.connection() as conn:
        await integrations.edit(
            conn, principal, integration_id, body.model_dump(exclude_unset=True)
        )
        return await _one(conn, integration_id)


@router.delete("/{integration_id}", status_code=status.HTTP_204_NO_CONTENT)
async def uninstall(integration_id: UUID, request: Request, principal: SteppedUpAdmin) -> None:
    async with request.app.state.db_pool.connection() as conn:
        await integrations.uninstall(conn, principal, integration_id)


@router.post("/{integration_id}/refresh", response_model=AdminIntegrationOut)
async def refresh(
    integration_id: UUID, request: Request, principal: SteppedUpAdmin
) -> AdminIntegrationOut:
    """Discover again, with the admin's own credential or the shared one. New tools start off."""
    state = request.app.state
    async with state.db_pool.connection() as conn:
        integration = await integrations.get(conn, integration_id)
        credential = await integrations.discovery_credential(conn, integration, principal)
    found = await state.gateway.discover(integrations.gateway_view(integration), credential)
    discovery = DiscoveryIn.model_validate(found).model_dump()
    async with state.db_pool.connection() as conn:
        await integrations.store_discovery(conn, principal, integration_id, discovery)
        return await _one(conn, integration_id)


@router.post("/{integration_id}/check", response_model=IntegrationCheckOut)
async def check(
    integration_id: UUID, request: Request, principal: SteppedUpAdmin
) -> IntegrationCheckOut:
    """Ask the server without a credential: is it there, what sign-in it wants, which certificate."""
    state = request.app.state
    async with state.db_pool.connection() as conn:
        integration = await integrations.get(conn, integration_id)
    found = await state.gateway.check(integrations.gateway_view(integration))
    if isinstance(found.get("detail"), str):
        found["detail"] = found["detail"][:1000]
    try:
        out = IntegrationCheckOut.model_validate(found)
    except ValidationError as exc:
        raise Unavailable("gateway_unavailable") from exc
    out.this_machine = integrations.on_this_machine(
        integration["url"], state.settings.lan_addresses
    )
    return out


@router.patch("/{integration_id}/tools", response_model=AdminIntegrationOut)
async def change_tools(
    integration_id: UUID,
    body: AdminIntegrationToolChanges,
    request: Request,
    principal: SteppedUpAdmin,
) -> AdminIntegrationOut:
    changes = [c.model_dump(exclude_none=True) for c in body.changes]
    async with request.app.state.db_pool.connection() as conn:
        await integrations.change_admin_tools(conn, principal, integration_id, changes)
        return await _one(conn, integration_id)


@router.put("/{integration_id}/shared-credential", response_model=ConnectionOut)
async def set_shared_credential(
    integration_id: UUID, body: CredentialIn, request: Request, principal: SteppedUpAdmin
) -> ConnectionOut:
    """The household's token or API key: the gateway encrypts it, only the ciphertext is kept.

    The server is asked first; a token it refuses is `credential_rejected`, not stored.
    """
    state = request.app.state
    async with state.db_pool.connection() as conn:
        integration = await integrations.get(conn, integration_id)
    if integration["credential_model"] != "shared":
        raise InvalidInput("wrong_credential_model")
    if integration["auth_method"] != "token":
        raise InvalidInput("auth_method_mismatch")
    ciphertext = await state.gateway.encrypt(integration_id, None, body.secret)
    check = await integrations.check_credential(state.gateway, integration, None, ciphertext)
    async with state.db_pool.connection() as conn:
        await integrations.store_credential(conn, principal, integration_id, None, ciphertext)
    return ConnectionOut(status="connected", **check)


@router.post("/{integration_id}/oauth/start", response_model=OAuthStartOut)
async def start_shared_sign_in(
    integration_id: UUID, request: Request, principal: SteppedUpAdmin, app: bool = False
) -> OAuthStartOut:
    """The household's sign-in: open `authorize_url`; the return page stores it as shared."""
    async with request.app.state.db_pool.connection() as conn:
        integration = await integrations.get(conn, integration_id)
    if integration["credential_model"] != "shared":
        raise InvalidInput("wrong_credential_model")
    return await oauth_pages.start(request, principal, integration_id, None, app)


@router.delete("/{integration_id}/shared-credential", status_code=status.HTTP_204_NO_CONTENT)
async def clear_shared_credential(
    integration_id: UUID, request: Request, principal: SteppedUpAdmin
) -> None:
    async with request.app.state.db_pool.connection() as conn:
        integration = await integrations.get(conn, integration_id)
        await integration_oauth.revoke(conn, request.app.state.gateway, integration, None)
        await integrations.delete_credential(conn, principal, integration_id, None)


@router.delete("/{integration_id}/connections/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def disconnect_user(
    integration_id: UUID, user_id: UUID, request: Request, principal: SteppedUpAdmin
) -> None:
    async with request.app.state.db_pool.connection() as conn:
        integration = await integrations.get(conn, integration_id)
        await integration_oauth.revoke(conn, request.app.state.gateway, integration, user_id)
        await integrations.delete_credential(conn, principal, integration_id, user_id)

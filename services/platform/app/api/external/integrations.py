"""`/api/platform/integrations`: the integrations a user may use (M18-03, `app.core.integrations`).

The list is readable by the agent too (it holds no credential). Connecting,
disconnecting and changing tool settings are for the user in person.
"""

from uuid import UUID

from fastapi import APIRouter, Request, status

from app.api.schemas import ConnectionOut, CredentialIn, IntegrationList, IntegrationToolChanges
from app.core import integrations
from app.core.errors import InvalidInput, NotFound
from app.core.principal import CurrentUser, HumanUser

router = APIRouter(prefix="/integrations")


@router.get("", response_model=IntegrationList)
async def list_mine(request: Request, principal: CurrentUser) -> IntegrationList:
    async with request.app.state.db_pool.connection() as conn:
        return IntegrationList(integrations=await integrations.user_view(conn, principal.user_id))


@router.patch("/{integration_id}/tools", response_model=IntegrationList)
async def change_my_tools(
    integration_id: UUID, body: IntegrationToolChanges, request: Request, principal: HumanUser
) -> IntegrationList:
    changes = [c.model_dump(exclude_none=True) for c in body.changes]
    async with request.app.state.db_pool.connection() as conn:
        await integrations.change_my_tools(conn, principal, integration_id, changes)
        return IntegrationList(integrations=await integrations.user_view(conn, principal.user_id))


@router.put("/{integration_id}/connection", response_model=ConnectionOut)
async def connect(
    integration_id: UUID, body: CredentialIn, request: Request, principal: HumanUser
) -> ConnectionOut:
    """Connect with a token or API key: the gateway encrypts it, only the ciphertext is kept.

    The server is asked first; a token it refuses is `credential_rejected`, not stored.
    """
    state = request.app.state
    async with state.db_pool.connection() as conn:
        integration = await integrations.get(conn, integration_id)
    if not integrations.usable_by(integration, principal.user_id):
        raise NotFound("integration_not_found")
    if integration["credential_model"] != "per_user":
        raise InvalidInput("wrong_credential_model")
    if integration["auth_method"] != "token":
        raise InvalidInput("auth_method_mismatch")
    ciphertext = await state.gateway.encrypt(integration_id, principal.user_id, body.secret)
    check = await integrations.check_credential(
        state.gateway, integration, principal.user_id, ciphertext
    )
    async with state.db_pool.connection() as conn:
        await integrations.store_credential(
            conn, principal, integration_id, principal.user_id, ciphertext
        )
    return ConnectionOut(status="connected", **check)


@router.delete("/{integration_id}/connection", status_code=status.HTTP_204_NO_CONTENT)
async def disconnect(integration_id: UUID, request: Request, principal: HumanUser) -> None:
    async with request.app.state.db_pool.connection() as conn:
        await integrations.delete_credential(conn, principal, integration_id, principal.user_id)

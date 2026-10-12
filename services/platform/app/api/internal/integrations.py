"""`/internal/*` for the integrations gateway (M18-03, `app.core.integrations`)."""

from uuid import UUID

from fastapi import APIRouter, Depends, Request, status

from app.api.internal.service_auth import GATEWAY, require_service
from app.api.schemas import (
    CredentialStatusIn,
    DiscoveryIn,
    IntegrationGrantOut,
    IntegrationGrantRequest,
)
from app.core import integrations
from app.core import principal as principals

router = APIRouter(dependencies=[Depends(require_service(GATEWAY))])


@router.post("/integration-grants", response_model=IntegrationGrantOut)
async def grant(body: IntegrationGrantRequest, request: Request) -> IntegrationGrantOut:
    """A run's delegation in; the server, the credential's ciphertext and the tools that apply out."""
    state = request.app.state
    async with state.db_pool.connection() as conn:
        agent = await principals.from_delegation(conn, state.tokens, body.delegation)
        return IntegrationGrantOut.model_validate(
            await integrations.grant(conn, agent, body.integration)
        )


@router.put("/integrations/{integration_id}/discovery", status_code=status.HTTP_204_NO_CONTENT)
async def store_discovery(integration_id: UUID, body: DiscoveryIn, request: Request) -> None:
    """The server reported a changed list and the gateway discovered again. New tools start off."""
    async with request.app.state.db_pool.connection() as conn:
        await integrations.store_discovery(conn, None, integration_id, body.model_dump())


@router.post(
    "/integrations/{integration_id}/credential-status", status_code=status.HTTP_204_NO_CONTENT
)
async def credential_status(
    integration_id: UUID, body: CredentialStatusIn, request: Request
) -> None:
    async with request.app.state.db_pool.connection() as conn:
        await integrations.mark_needs_reconnecting(conn, integration_id, body.user_id)

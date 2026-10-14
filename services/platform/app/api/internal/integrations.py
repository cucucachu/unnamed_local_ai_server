"""`/internal/*` for the integrations gateway (M18-03, `app.core.integrations`)."""

from uuid import UUID

from fastapi import APIRouter, Depends, Request, status

from app.api.internal.service_auth import GATEWAY, require_service
from app.api.schemas import (
    CredentialStatusIn,
    DiscoveryIn,
    IntegrationGrantOut,
    IntegrationGrantRequest,
    OAuthDiscoveredIn,
    RefreshedCredentialIn,
)
from app.core import integration_oauth, integrations
from app.core import principal as principals
from app.core.integrations_gateway import decode_ciphertext

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


@router.put("/integrations/{integration_id}/oauth", status_code=status.HTTP_204_NO_CONTENT)
async def store_oauth(integration_id: UUID, body: OAuthDiscoveredIn, request: Request) -> None:
    """A server's OAuth metadata, read by the gateway: its endpoints join the egress rules."""
    summary = body.summary.model_dump() if body.summary else None
    async with request.app.state.db_pool.connection() as conn:
        await integration_oauth.store_discovered(conn, integration_id, summary, body.endpoints)


@router.put("/integrations/{integration_id}/credential", status_code=status.HTTP_204_NO_CONTENT)
async def store_refreshed(
    integration_id: UUID, body: RefreshedCredentialIn, request: Request
) -> None:
    """The gateway refreshed an OAuth token (refresh tokens may rotate). Replaces, never adds."""
    ciphertext = decode_ciphertext(body.ciphertext)
    async with request.app.state.db_pool.connection() as conn:
        await integration_oauth.store_refreshed(conn, integration_id, body.user_id, ciphertext)

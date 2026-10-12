"""Approval markers and audit for integration calls (M18-04, `app.core.hitl`, docs/INTEGRATIONS.md §7)."""

from fastapi import APIRouter, Depends, Request, status

from app.api.internal.service_auth import AGENT, GATEWAY, require_service
from app.api.schemas import (
    HitlApprovalOut,
    IntegrationApprovalConsume,
    IntegrationApprovalRequest,
    IntegrationAuditIn,
)
from app.core import audit, integrations
from app.core import principal as principals
from app.core.errors import NotFound

router = APIRouter()


async def _usable(conn, agent, slug: str, tool: str) -> dict:
    integration = await integrations.by_slug(conn, slug)
    if not integrations.usable_by(integration, agent.user_id):
        raise NotFound("integration_not_found")
    if tool not in await integrations.tool_names(conn, integration["id"]):
        raise NotFound("tool_not_found")
    return integration


@router.post(
    "/integration-approvals",
    response_model=HitlApprovalOut,
    dependencies=[Depends(require_service(AGENT))],
)
async def mint(body: IntegrationApprovalRequest, request: Request) -> HitlApprovalOut:
    """A single-use marker for one integration call the user approved: this tool, these arguments."""
    state = request.app.state
    async with state.db_pool.connection() as conn:
        agent = await principals.from_delegation(conn, state.tokens, body.delegation)
        integration = await _usable(conn, agent, body.integration, body.tool)
    token, ttl_s = state.hitl.mint_call(agent, integration["id"], body.tool, body.arguments)
    return HitlApprovalOut(token=token, expires_in_s=ttl_s)


@router.post(
    "/integration-approvals/consume",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_service(GATEWAY))],
)
async def consume(body: IntegrationApprovalConsume, request: Request) -> None:
    """Spend a marker on the call it's for; `403 hitl_approval_required` otherwise."""
    state = request.app.state
    async with state.db_pool.connection() as conn:
        agent = await principals.from_delegation(conn, state.tokens, body.delegation)
        integration = await integrations.by_slug(conn, body.integration)
    state.hitl.consume_call(body.token, agent, integration["id"], body.tool, body.arguments)


_VERBS = {
    "open": "Opened",
    "call": "Called",
    "read": "Read",
    "prompt": "Got prompt",
}


@router.post(
    "/integration-audit",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_service(GATEWAY))],
)
async def record(body: IntegrationAuditIn, request: Request) -> None:
    """One thing the agent did through the gateway, attributed to the delegation's user."""
    state = request.app.state
    async with state.db_pool.connection() as conn:
        agent = await principals.from_delegation(conn, state.tokens, body.delegation)
        integration = await integrations.by_slug(conn, body.integration)
        what = f" {body.target}" if body.target else ""
        outcome = "" if body.status == "ok" else f" (failed: {body.error_code or 'error'})"
        await audit.record(
            conn,
            agent,
            f"integrations.{body.action}",
            f"{_VERBS[body.action]}{what} on {integration['name']}{outcome}",
            target_type="integration",
            target_id=integration["id"],
            detail=body.model_dump(exclude={"delegation", "integration", "action"}),
        )

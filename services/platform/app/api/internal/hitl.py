from fastapi import APIRouter, Depends, Request

from app.api.internal.service_auth import AGENT, require_service
from app.api.schemas import HitlApprovalOut, HitlApprovalRequest
from app.core import principal as principals

router = APIRouter(dependencies=[Depends(require_service(AGENT))])


@router.post("/hitl-approvals", response_model=HitlApprovalOut)
async def mint(body: HitlApprovalRequest, request: Request) -> HitlApprovalOut:
    """A single-use marker for one agent approve call, after the user approved it (`app.core.hitl`).

    Only for a migration the delegation's user could approve right now.
    """
    state = request.app.state
    async with state.db_pool.connection() as conn:
        agent = await principals.from_delegation(conn, state.tokens, body.delegation)
    await state.appdata.pending(agent, body.instance_id, body.migration_id)
    token, ttl_s = state.hitl.mint(agent, body.instance_id, body.migration_id)
    return HitlApprovalOut(token=token, expires_in_s=ttl_s)

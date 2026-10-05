from fastapi import APIRouter, Depends, Request, status

from app.api.internal.service_auth import AGENT, require_service
from app.api.schemas import (
    DelegationOut,
    RoutineGrantExchangeRequest,
    RoutineGrantOut,
    RoutineGrantRequest,
    RoutineGrantRevokeRequest,
)
from app.core import routine_grants

router = APIRouter(prefix="/routine-grants", dependencies=[Depends(require_service(AGENT))])


@router.post("", response_model=RoutineGrantOut)
async def issue(body: RoutineGrantRequest, request: Request) -> RoutineGrantOut:
    """A logged-in user's identity token in, a grant for one routine in one space out."""
    async with request.app.state.db_pool.connection() as conn:
        grant, grant_id, space = await routine_grants.issue(
            conn,
            request.app.state.tokens,
            body.identity_token,
            body.routine_id,
            body.space,
            body.label,
        )
    return RoutineGrantOut(grant=grant, grant_id=grant_id, space=space)


@router.post("/exchange", response_model=DelegationOut)
async def exchange(body: RoutineGrantExchangeRequest, request: Request) -> DelegationOut:
    """A grant in, a delegation for one of its routine's run threads out (401 once it's dead)."""
    async with request.app.state.db_pool.connection() as conn:
        token, expires_at = await routine_grants.exchange(
            conn, request.app.state.tokens, body.grant, body.routine_id, body.thread_id
        )
    return DelegationOut(token=token, expires_at=expires_at)


@router.post("/revoke", status_code=status.HTTP_204_NO_CONTENT)
async def revoke(body: RoutineGrantRevokeRequest, request: Request) -> None:
    async with request.app.state.db_pool.connection() as conn:
        await routine_grants.revoke(conn, body.grant)

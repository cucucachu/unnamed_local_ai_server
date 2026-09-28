from fastapi import APIRouter, Depends, Request

from app.api.internal.service_auth import AGENT, require_service
from app.api.schemas import DelegationOut, DelegationRefreshRequest, DelegationRequest
from app.core import delegations

router = APIRouter(prefix="/delegations", dependencies=[Depends(require_service(AGENT))])


@router.post("", response_model=DelegationOut)
async def exchange(body: DelegationRequest, request: Request) -> DelegationOut:
    """Identity token (`act=user`) in, delegation (`act=agent`) out, for one thread's run."""
    async with request.app.state.db_pool.connection() as conn:
        token, expires_at = await delegations.exchange(
            conn, request.app.state.tokens, body.identity_token, body.thread_id
        )
    return DelegationOut(token=token, expires_at=expires_at)


@router.post("/refresh", response_model=DelegationOut)
async def refresh(body: DelegationRefreshRequest, request: Request) -> DelegationOut:
    """A still-valid (or just-expired) delegation in, a fresh one out, while its session lives."""
    async with request.app.state.db_pool.connection() as conn:
        token, expires_at = await delegations.refresh(conn, request.app.state.tokens, body.token)
    return DelegationOut(token=token, expires_at=expires_at)

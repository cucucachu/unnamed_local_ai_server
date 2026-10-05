from fastapi import APIRouter, Depends, Request

from app.api.internal.service_auth import AGENT, require_service
from app.api.schemas import SpaceAccessOut, SpaceAccessRequest
from app.core import delegations

router = APIRouter(dependencies=[Depends(require_service(AGENT))])


@router.post("/space-access", response_model=SpaceAccessOut)
async def space_access(body: SpaceAccessRequest, request: Request) -> SpaceAccessOut:
    """The calling user's role in a space (`/personal` or `/spaces/<slug>`), from their
    identity token or a chat's delegation.

    agent-server asks before saving a routine that will run in that space.
    """
    async with request.app.state.db_pool.connection() as conn:
        space, role = await delegations.space_role(
            conn,
            request.app.state.tokens,
            body.space,
            identity_token=body.identity_token,
            delegation_token=body.delegation_token,
        )
    return SpaceAccessOut(space=space, role=role)

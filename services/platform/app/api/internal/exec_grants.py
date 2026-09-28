from fastapi import APIRouter, Depends, Request

from app.api.internal.service_auth import EXEC, require_service
from app.api.schemas import ExecGrantsOut, ExecGrantsRequest
from app.core import exec_grants

router = APIRouter(dependencies=[Depends(require_service(EXEC))])


@router.post("/exec-grants", response_model=ExecGrantsOut)
async def grants(body: ExecGrantsRequest, request: Request) -> ExecGrantsOut:
    """A run's delegation in, the uid/gids/mounts its exec container gets out."""
    state = request.app.state
    async with state.db_pool.connection() as conn:
        result = await exec_grants.for_delegation(
            conn, state.tokens, state.storage, state.settings.spaces_host_dir, body.delegation
        )
    return ExecGrantsOut.model_validate(result)

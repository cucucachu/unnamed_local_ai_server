from fastapi import APIRouter, Depends, Request

from app.api.internal.service_auth import AGENT, require_service
from app.core.bootstrap import bootstrap_admin_id
from app.core.errors import NotFound

router = APIRouter(dependencies=[Depends(require_service(AGENT))])


@router.get("/bootstrap-admin")
async def bootstrap_admin(request: Request) -> dict[str, str]:
    """The first admin's id, so agent-server can hand them pre-Stage-3 threads."""
    async with request.app.state.db_pool.connection() as conn:
        user_id = await bootstrap_admin_id(conn)
    if user_id is None:
        raise NotFound("bootstrap_pending")
    return {"user_id": user_id}

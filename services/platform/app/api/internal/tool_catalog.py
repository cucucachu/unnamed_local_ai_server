from fastapi import APIRouter, Depends, Request, status

from app.api.internal.service_auth import AGENT, require_service
from app.api.schemas import ToolCatalogIn
from app.core import tool_permissions

router = APIRouter(dependencies=[Depends(require_service(AGENT))])


@router.put("/tool-catalog", status_code=status.HTTP_204_NO_CONTENT)
async def replace(body: ToolCatalogIn, request: Request) -> None:
    """agent-server's tool catalog, sent at its startup (`app.core.tool_permissions`)."""
    tools = [
        tool_permissions.Tool(t.name, t.label, t.description, t.group, t.read_only)
        for t in body.tools
    ]
    async with request.app.state.db_pool.connection() as conn:
        await tool_permissions.replace_catalog(conn, tools)

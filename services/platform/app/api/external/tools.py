"""`/api/platform/tools`: agent tool permissions (#342, `app.core.tool_permissions`).

The user's own list and changes are for the user in person: the agent can
read what applies to its run (`/tools/policy`) but never change it. The
admin's settings are on the admin router.
"""

from fastapi import APIRouter, Request

from app.api.schemas import ToolPolicyOut, UserToolChanges, UserToolList
from app.core import tool_permissions
from app.core.principal import CurrentUser, HumanUser

router = APIRouter(prefix="/tools")


@router.get("", response_model=UserToolList)
async def list_mine(request: Request, principal: HumanUser) -> UserToolList:
    async with request.app.state.db_pool.connection() as conn:
        return UserToolList(tools=await tool_permissions.user_view(conn, principal.user_id))


@router.patch("", response_model=UserToolList)
async def change_mine(
    body: UserToolChanges, request: Request, principal: HumanUser
) -> UserToolList:
    changes = [c.model_dump(exclude_none=True) for c in body.changes]
    async with request.app.state.db_pool.connection() as conn:
        await tool_permissions.change_mine(conn, principal, changes)
        return UserToolList(tools=await tool_permissions.user_view(conn, principal.user_id))


@router.get("/policy", response_model=ToolPolicyOut)
async def policy(request: Request, principal: CurrentUser) -> ToolPolicyOut:
    async with request.app.state.db_pool.connection() as conn:
        return ToolPolicyOut(tools=await tool_permissions.policy(conn, principal.user_id))

"""`/api/platform/admin/*`: users, spaces, and invites. Admin, in person, stepped up.

Admins manage space membership through the ordinary member routes
(`spaces.authorize_membership`); nothing here grants access to space data.
"""

from urllib.parse import urlencode
from uuid import UUID

from fastapi import APIRouter, Depends, Request, status

from app.api.schemas import (
    AdminUserPatch,
    InviteCreated,
    InviteCreateRequest,
    InviteList,
    InviteOut,
    SpaceList,
    UserList,
    UserOut,
)
from app.api.session_http import is_https
from app.core import invites, spaces, users
from app.core.principal import SteppedUpAdmin, require_admin_stepped_up

router = APIRouter(prefix="/admin", dependencies=[Depends(require_admin_stepped_up)])

INVITE_PATH = "/invite"


def _accept_url(request: Request, token: str) -> str:
    """Absolute URL of the web app's invite screen, as the admin's browser reached us."""
    host = request.headers.get("x-forwarded-host") or request.headers.get("host") or "homeai.local"
    scheme = "https" if is_https(request) else "http"
    return f"{scheme}://{host.split(',')[0].strip()}{INVITE_PATH}?{urlencode({'token': token})}"


@router.get("/users", response_model=UserList)
async def list_users(request: Request):
    async with request.app.state.db_pool.connection() as conn:
        return UserList(users=await users.list_users(conn))


@router.patch("/users/{user_id}", response_model=UserOut)
async def patch_user(user_id: UUID, body: AdminUserPatch, request: Request):
    async with request.app.state.db_pool.connection() as conn:
        return await users.update_user(conn, user_id, role=body.role, disabled=body.disabled)


@router.get("/spaces", response_model=SpaceList)
async def list_spaces(request: Request, principal: SteppedUpAdmin):
    async with request.app.state.db_pool.connection() as conn:
        return SpaceList(spaces=await spaces.list_all_spaces(conn, principal.user_id))


@router.post("/invites", response_model=InviteCreated, status_code=status.HTTP_201_CREATED)
async def create_invite(
    body: InviteCreateRequest,
    request: Request,
    principal: SteppedUpAdmin,
):
    async with request.app.state.db_pool.connection() as conn:
        token, row = await invites.create_invite(conn, principal.user_id, body.label)
    return InviteCreated(**row, token=token, accept_url=_accept_url(request, token))


@router.get("/invites", response_model=InviteList)
async def list_invites(request: Request):
    async with request.app.state.db_pool.connection() as conn:
        return InviteList(invites=[InviteOut(**row) for row in await invites.list_invites(conn)])


@router.delete("/invites/{invite_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_invite(invite_id: UUID, request: Request) -> None:
    async with request.app.state.db_pool.connection() as conn:
        await invites.revoke_invite(conn, invite_id)

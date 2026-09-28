"""`/api/platform/spaces*`: spaces and their members (docs/PLATFORM.md §5).

Space routes authorize through `spaces.authorize_space`; member routes
through `spaces.authorize_membership`, which alone adds the stepped-up admin
override. Creating, renaming, archiving, and every membership change are
human-only (`manage` is never granted to an agent delegation).
"""

from uuid import UUID

import anyio.to_thread
from fastapi import APIRouter, Request, status

from app.api.schemas import (
    MemberAddRequest,
    MemberList,
    MemberOut,
    MemberPatchRequest,
    SpaceCreateRequest,
    SpaceList,
    SpaceOut,
    SpacePatchRequest,
)
from app.core import appbuild, spaces
from app.core.principal import CurrentUser, HumanUser

router = APIRouter(prefix="/spaces")


def _out(access: spaces.SpaceAccess) -> SpaceOut:
    return SpaceOut(**access.space, role=access.role)


@router.get("", response_model=SpaceList)
async def list_my_spaces(request: Request, principal: CurrentUser):
    async with request.app.state.db_pool.connection() as conn:
        return SpaceList(spaces=await spaces.list_user_spaces(conn, principal.user_id))


@router.post("", response_model=SpaceOut, status_code=status.HTTP_201_CREATED)
async def create_space(body: SpaceCreateRequest, request: Request, principal: HumanUser):
    async with request.app.state.db_pool.connection() as conn:
        space = await spaces.create_shared_space(
            conn,
            slug=body.slug,
            name=body.name,
            owner_id=principal.user_id,
            storage=request.app.state.storage,
        )
    return SpaceOut(**space, role="owner")


@router.get("/{space_id}", response_model=SpaceOut)
async def get_space(space_id: UUID, request: Request, principal: CurrentUser):
    async with request.app.state.db_pool.connection() as conn:
        return _out(await spaces.authorize_space(conn, principal, space_id, "read"))


@router.patch("/{space_id}", response_model=SpaceOut)
async def patch_space(
    space_id: UUID, body: SpacePatchRequest, request: Request, principal: CurrentUser
):
    async with request.app.state.db_pool.connection() as conn:
        access = await spaces.authorize_space(conn, principal, space_id, "manage")
        space = await spaces.rename_space(conn, space_id, body.name)
    return SpaceOut(**space, role=access.role)


@router.delete("/{space_id}", status_code=status.HTTP_204_NO_CONTENT)
async def archive_space(space_id: UUID, request: Request, principal: CurrentUser) -> None:
    async with request.app.state.db_pool.connection() as conn:
        access = await spaces.authorize_space(conn, principal, space_id, "manage")
        async with conn.transaction():
            await spaces.archive_space(conn, access.space)
            bundles = await appbuild.release_space_bundles(conn, space_id)
    await anyio.to_thread.run_sync(
        appbuild.drop_bundles, request.app.state.settings.platform_data_dir, bundles
    )


@router.get("/{space_id}/members", response_model=MemberList)
async def list_members(space_id: UUID, request: Request, principal: CurrentUser):
    async with request.app.state.db_pool.connection() as conn:
        await spaces.authorize_membership(conn, principal, space_id, "read")
        return MemberList(members=await spaces.list_members(conn, space_id))


@router.post("/{space_id}/members", response_model=MemberOut, status_code=status.HTTP_201_CREATED)
async def add_member(
    space_id: UUID, body: MemberAddRequest, request: Request, principal: CurrentUser
):
    async with request.app.state.db_pool.connection() as conn:
        access = await spaces.authorize_membership(conn, principal, space_id, "manage")
        return await spaces.add_member(conn, access.space, body.user_id, body.role)


@router.patch("/{space_id}/members/{user_id}", response_model=MemberOut)
async def patch_member(
    space_id: UUID,
    user_id: UUID,
    body: MemberPatchRequest,
    request: Request,
    principal: CurrentUser,
):
    async with request.app.state.db_pool.connection() as conn:
        access = await spaces.authorize_membership(conn, principal, space_id, "manage")
        return await spaces.set_member_role(conn, access.space, user_id, body.role)


@router.delete("/{space_id}/members/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_member(
    space_id: UUID, user_id: UUID, request: Request, principal: CurrentUser
) -> None:
    async with request.app.state.db_pool.connection() as conn:
        access = await spaces.authorize_membership(conn, principal, space_id, "manage")
        await spaces.remove_member(conn, access.space, user_id)

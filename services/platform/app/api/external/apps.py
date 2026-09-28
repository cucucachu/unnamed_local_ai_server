"""`/api/platform/apps*` and `/api/platform/spaces/{id}/instances*`: the app registry.

Contract: docs/ARCHITECTURE.md §3 "Apps"; rules: `app.core.apps`. Every
route takes an agent delegation too (D6).
"""

from uuid import UUID

from fastapi import APIRouter, Request, status

from app.api.schemas import (
    AppList,
    AppOut,
    AppRegisterRequest,
    AppValidationOut,
    InstallRequest,
    InstanceList,
    InstanceOut,
)
from app.core import apps, manifest
from app.core.principal import CurrentUser

router = APIRouter()


@router.get("/apps/schema")
async def app_schema(principal: CurrentUser) -> dict:
    """The `app.json` JSON Schema (draft 2020-12)."""
    return manifest.SCHEMA


@router.get("/apps", response_model=AppList)
async def list_apps(request: Request, principal: CurrentUser):
    async with request.app.state.db_pool.connection() as conn:
        return AppList(apps=await apps.list_visible_apps(conn, principal))


@router.post("/apps", response_model=AppValidationOut, status_code=status.HTTP_201_CREATED)
async def register_app(body: AppRegisterRequest, request: Request, principal: CurrentUser):
    async with request.app.state.db_pool.connection() as conn:
        app = await apps.register_app(conn, principal, request.app.state.storage, body.source_path)
    return AppValidationOut(app=app, valid=True, diagnostics=[])


@router.get("/apps/{app_id}", response_model=AppOut)
async def get_app(app_id: UUID, request: Request, principal: CurrentUser):
    async with request.app.state.db_pool.connection() as conn:
        return await apps.get_visible_app(conn, principal, app_id)


@router.post("/apps/{app_id}/validate", response_model=AppValidationOut)
async def validate_app(app_id: UUID, request: Request, principal: CurrentUser):
    async with request.app.state.db_pool.connection() as conn:
        app, diagnostics = await apps.validate_app(
            conn, principal, request.app.state.storage, app_id
        )
    return AppValidationOut(app=app, valid=not diagnostics, diagnostics=diagnostics)


@router.get("/spaces/{space_id}/instances", response_model=InstanceList)
async def list_instances(space_id: UUID, request: Request, principal: CurrentUser):
    async with request.app.state.db_pool.connection() as conn:
        return InstanceList(instances=await apps.list_instances(conn, principal, space_id))


@router.post(
    "/spaces/{space_id}/instances",
    response_model=InstanceOut,
    status_code=status.HTTP_201_CREATED,
)
async def install_app(
    space_id: UUID, body: InstallRequest, request: Request, principal: CurrentUser
):
    async with request.app.state.db_pool.connection() as conn:
        return await apps.install_app(
            conn, principal, request.app.state.storage, space_id, body.app_id, body.tracks
        )


@router.delete("/spaces/{space_id}/instances/{instance_id}", status_code=status.HTTP_204_NO_CONTENT)
async def uninstall_app(
    space_id: UUID, instance_id: UUID, request: Request, principal: CurrentUser
) -> None:
    async with request.app.state.db_pool.connection() as conn:
        await apps.uninstall_app(conn, principal, request.app.state.storage, space_id, instance_id)

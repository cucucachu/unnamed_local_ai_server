"""`/api/platform/apps*` and `/api/platform/spaces/{id}/instances*`: the app registry.

Contract: docs/ARCHITECTURE.md §3 "Apps"; rules: `app.core.apps`. Every
route takes an agent delegation too (D6).
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query, Request, status

from app.api.schemas import (
    AppBuildOut,
    AppHistoryOut,
    AppList,
    AppOut,
    AppRegisterRequest,
    AppRevertOut,
    AppRevertRequest,
    AppValidationOut,
    CatalogList,
    ForkOut,
    ForkRequest,
    InstallRequest,
    InstanceList,
    InstanceOut,
    PublishOut,
    PublishRequest,
    UpdateInstanceOut,
    UpdateInstanceRequest,
)
from app.core import appbuild, apphistory, apps, manifest
from app.core.errors import Unavailable
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


@router.post("/apps/{app_id}/build", response_model=AppBuildOut)
async def build_app(app_id: UUID, request: Request, principal: CurrentUser):
    """Build the working version in the sandboxed builder (`app.core.appbuild`)."""
    state = request.app.state
    if state.builds is None:
        raise Unavailable("builder_unavailable")
    app, build, diagnostics, migrations = await appbuild.build_app(
        state.db_pool, principal, state.storage, state.builds, state.builder,
        state.settings.platform_data_dir, state.appdata, state.history, app_id,
    )  # fmt: skip
    return AppBuildOut(
        app=app,
        ok=build is not None and build.ok,
        build=None if build is None else vars(build),
        diagnostics=diagnostics,
        migrations=migrations,
    )


@router.get("/apps/{app_id}/history", response_model=AppHistoryOut)
async def app_history(
    app_id: UUID,
    request: Request,
    principal: CurrentUser,
    offset: Annotated[int, Query(ge=0, le=1_000_000)] = 0,
    limit: Annotated[int, Query(ge=1, le=apphistory.MAX_LOG_LIMIT)] = 50,
):
    """The app's source history, newest first (`app.core.apphistory`)."""
    state = request.app.state
    if state.history is None:
        raise Unavailable("history_unavailable")
    commits, next_offset = await apphistory.list_history(
        state.db_pool, principal, state.history, app_id, offset, limit
    )
    return AppHistoryOut(commits=commits, next_offset=next_offset)


@router.post("/apps/{app_id}/revert", response_model=AppRevertOut)
async def revert_app(
    app_id: UUID, body: AppRevertRequest, request: Request, principal: CurrentUser
):
    """Restore the source as of `commit`, as a new commit, and rebuild."""
    state = request.app.state
    if state.builds is None:
        raise Unavailable("builder_unavailable")
    if state.history is None:
        raise Unavailable("history_unavailable")
    commit, app, build, diagnostics, migrations = await appbuild.revert_app(
        state.db_pool, principal, state.storage, state.builds, state.builder,
        state.settings.platform_data_dir, state.appdata, state.history, app_id, body.commit,
    )  # fmt: skip
    return AppRevertOut(
        commit=commit,
        app=app,
        ok=build is not None and build.ok,
        build=None if build is None else vars(build),
        diagnostics=diagnostics,
        migrations=migrations,
    )


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
            conn,
            principal,
            request.app.state.storage,
            space_id,
            body.app_id,
            body.tracks,
            body.granted_permissions,
            body.granted_reads,
        )


@router.delete("/spaces/{space_id}/instances/{instance_id}", status_code=status.HTTP_204_NO_CONTENT)
async def uninstall_app(
    space_id: UUID, instance_id: UUID, request: Request, principal: CurrentUser
) -> None:
    await request.app.state.appdata.uninstall(principal, space_id, instance_id)


@router.post("/apps/{app_id}/publish", response_model=PublishOut)
async def publish_app(app_id: UUID, body: PublishRequest, request: Request, principal: CurrentUser):
    """Snapshot the working copy as a published version listed in `space_ids`."""
    async with request.app.state.db_pool.connection() as conn:
        app, version, space_ids = await apps.publish_app(
            conn,
            principal,
            request.app.state.storage,
            request.app.state.settings.platform_data_dir,
            app_id,
            body.space_ids,
        )
    return PublishOut(app=app, version=version, space_ids=space_ids)


@router.get("/spaces/{space_id}/catalog", response_model=CatalogList)
async def space_catalog(space_id: UUID, request: Request, principal: CurrentUser):
    async with request.app.state.db_pool.connection() as conn:
        return CatalogList(entries=await apps.list_catalog(conn, principal, space_id))


@router.post("/spaces/{space_id}/instances/{instance_id}/update", response_model=UpdateInstanceOut)
async def update_instance(
    space_id: UUID,
    instance_id: UUID,
    body: UpdateInstanceRequest,
    request: Request,
    principal: CurrentUser,
):
    """Pin a published install to a newer version and migrate its database."""
    state = request.app.state
    async with state.db_pool.connection() as conn:
        instance = await apps.update_instance(
            conn,
            principal,
            space_id,
            instance_id,
            body.version_id,
            body.granted_permissions,
            body.granted_reads,
        )
    migration = await state.appdata.migrate(principal, instance_id)
    state.events.app_built([instance["space_id"]], instance["app_id"], instance["app"]["version"])
    return UpdateInstanceOut(instance=instance, migration=migration)


@router.post("/apps/{app_id}/fork", response_model=ForkOut, status_code=status.HTTP_201_CREATED)
async def fork_app(app_id: UUID, body: ForkRequest, request: Request, principal: CurrentUser):
    """Copy source (or the latest published snapshot) into `space_id` as a new app."""
    async with request.app.state.db_pool.connection() as conn:
        app, instance = await apps.fork_app(
            conn,
            principal,
            request.app.state.storage,
            request.app.state.settings.platform_data_dir,
            app_id,
            body.space_id,
            body.slug,
        )
    return ForkOut(app=app, instance=instance)

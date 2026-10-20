"""`/api/platform/apps/instances/{id}/...`: an app instance's bundle, data (RPC) and migrations.

Contract: docs/ARCHITECTURE.md §3 "App data" and "App runtime"; rules:
`app.core.appdata`, `app.core.appbuild.instance_bundle`.
Every route takes an agent delegation too (D6).
"""

import base64
import binascii
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Body, Request, status

from app.api.schemas import (
    ExportBody,
    ExportOut,
    ImageIn,
    ImportBody,
    ImportOut,
    InstanceBundleOut,
    MigrationList,
    MigrationOut,
    RpcRequest,
)
from app.core import appbuild, appimages, hitl
from app.core.errors import InvalidInput
from app.core.principal import CurrentUser

router = APIRouter(prefix="/apps/instances/{instance_id}")


@router.get("/bundle", response_model=InstanceBundleOut)
async def bundle(instance_id: UUID, request: Request, principal: CurrentUser):
    state = request.app.state
    return await appbuild.instance_bundle(
        state.db_pool, principal, state.settings.platform_data_dir, instance_id
    )


@router.post("/rpc")
async def rpc(
    instance_id: UUID, body: Annotated[RpcRequest, Body()], request: Request, principal: CurrentUser
) -> dict:
    return await request.app.state.appdata.rpc(principal, instance_id, body.model_dump())


@router.post("/images", status_code=status.HTTP_201_CREATED)
async def save_image(
    instance_id: UUID, body: ImageIn, request: Request, principal: CurrentUser
) -> dict:
    """A photo the user picked (#468), base64: a JPEG, PNG or WebP of at most 512 KB, so it
    still fits the bridge as a data URL."""
    try:
        data = base64.b64decode(body.data, validate=True)
    except binascii.Error:
        raise InvalidInput("not_an_image") from None
    image_id = await request.app.state.appdata.save_image(principal, instance_id, data)
    return {"id": image_id}


@router.get("/images/{image_id}")
async def get_image(
    instance_id: UUID, image_id: str, request: Request, principal: CurrentUser
) -> dict:
    """The photo as a `data:` URL, which is how the host hands it to the sandbox."""
    data = await request.app.state.appdata.read_image(principal, instance_id, image_id)
    encoded = base64.b64encode(data).decode("ascii")
    return {"data_url": f"data:{appimages.mime_of(image_id)};base64,{encoded}"}


@router.delete("/images/{image_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_image(
    instance_id: UUID, image_id: str, request: Request, principal: CurrentUser
) -> None:
    await request.app.state.appdata.delete_image(principal, instance_id, image_id)


@router.post("/export", response_model=ExportOut, status_code=201)
async def export(
    instance_id: UUID, body: ExportBody, request: Request, principal: CurrentUser
) -> ExportOut:
    """Every table as CSV plus `schema.sql` into a new folder in Files (M22-08)."""
    out = await request.app.state.appdata.export_csv(principal, instance_id, body.folder)
    request.app.state.file_index.changed(out["space_id"], tuple(out["rel"]))
    return ExportOut(folder=out["folder"], tables=out["tables"])


@router.post("/import", response_model=ImportOut)
async def import_csv(
    instance_id: UUID, body: ImportBody, request: Request, principal: CurrentUser
) -> ImportOut:
    """One CSV file in Files into one table, in one transaction (M22-08)."""
    return await request.app.state.appdata.import_csv(
        principal, instance_id, body.table, body.path, body.mapping, body.delimiter, body.dry_run
    )


@router.get("/migrations", response_model=MigrationList)
async def list_migrations(instance_id: UUID, request: Request, principal: CurrentUser):
    rows = await request.app.state.appdata.list_migrations(principal, instance_id)
    return MigrationList(migrations=rows)


@router.post("/migrate", response_model=MigrationOut)
async def migrate(instance_id: UUID, request: Request, principal: CurrentUser):
    return await request.app.state.appdata.migrate(principal, instance_id)


@router.post("/migrations/{migration_id}/approve", response_model=MigrationOut)
async def approve(instance_id: UUID, migration_id: UUID, request: Request, principal: CurrentUser):
    """An agent needs the HITL marker agent-server mints after the user approved (`app.core.hitl`)."""
    if principal.is_agent:
        request.app.state.hitl.consume(
            request.headers.get(hitl.HEADER), principal, instance_id, migration_id
        )
    return await request.app.state.appdata.approve(principal, instance_id, migration_id)


@router.post("/migrations/{migration_id}/reject", response_model=MigrationOut)
async def reject(instance_id: UUID, migration_id: UUID, request: Request, principal: CurrentUser):
    return await request.app.state.appdata.reject(principal, instance_id, migration_id)

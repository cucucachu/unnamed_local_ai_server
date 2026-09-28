"""`/api/platform/apps/instances/{id}/...`: an app instance's bundle, data (RPC) and migrations.

Contract: docs/ARCHITECTURE.md §3 "App data" and "App runtime"; rules:
`app.core.appdata`, `app.core.appbuild.instance_bundle`.
Every route takes an agent delegation too (D6).
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Body, Request

from app.api.schemas import InstanceBundleOut, MigrationList, MigrationOut, RpcRequest
from app.core import appbuild, hitl
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

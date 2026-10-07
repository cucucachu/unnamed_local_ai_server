"""`/api/platform/audit`: the audit log, newest first (docs/PLATFORM.md §4 "Audit log").

Members of a space read its events (`space_id`); only admins may leave it
out and read everything. Read-only: events are written by the platform
alone (`app.core.audit.record`).
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query, Request

from app.api.schemas import AuditEventList
from app.core import audit
from app.core.principal import CurrentUser

router = APIRouter(prefix="/audit")


@router.get("", response_model=AuditEventList)
async def list_audit_events(
    request: Request,
    principal: CurrentUser,
    space_id: UUID | None = None,
    target_type: Annotated[str | None, Query(max_length=64)] = None,
    target_id: Annotated[str | None, Query(max_length=128)] = None,
    actor_user_id: UUID | None = None,
    kind: Annotated[str | None, Query(max_length=128)] = None,
    before: Annotated[int | None, Query(ge=1)] = None,
    limit: Annotated[int, Query(ge=1, le=audit.MAX_LIMIT)] = audit.DEFAULT_LIMIT,
):
    async with request.app.state.db_pool.connection() as conn:
        events = await audit.list_events(
            conn, principal, space_id=space_id, target_type=target_type, target_id=target_id,
            actor_user_id=actor_user_id, kind=kind, before=before, limit=limit + 1,
        )  # fmt: skip
    more = len(events) > limit
    events = events[:limit]
    return AuditEventList(events=events, next_before=events[-1]["id"] if more and events else None)

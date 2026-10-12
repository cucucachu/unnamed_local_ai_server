"""`/internal/egress-rules` for the egress proxy (M18-05, docs/INTEGRATIONS.md §3)."""

from typing import Any

from fastapi import APIRouter, Depends, Request

from app.api.internal.service_auth import EGRESS, require_service
from app.core import integrations

router = APIRouter(dependencies=[Depends(require_service(EGRESS))])


@router.get("/egress-rules")
async def egress_rules(request: Request) -> dict[str, list[dict[str, Any]]]:
    """The destinations the integrations gateway may reach: one per installed integration."""
    async with request.app.state.db_pool.connection() as conn:
        return {"rules": await integrations.egress_rules(conn)}

"""Service-to-service routes under `/internal/*`.

Reachable only over `homeai-internal`; Caddy never routes `/internal/*`
(docs/PLATFORM.md §3). Everything here other than `health`, `jwks`, and
`auth/verify` requires a service bearer token (`service_auth.py`).
"""

from fastapi import APIRouter

from app.api.internal import (
    auth,
    bootstrap,
    delegations,
    egress,
    exec_grants,
    health,
    hitl,
    integration_approvals,
    integrations,
    jwks,
    routine_grants,
    space_access,
    tool_catalog,
)

router = APIRouter(prefix="/internal")
router.include_router(health.router)
router.include_router(jwks.router)
router.include_router(auth.router)
router.include_router(bootstrap.router)
router.include_router(delegations.router)
router.include_router(exec_grants.router)
router.include_router(hitl.router)
router.include_router(space_access.router)
router.include_router(routine_grants.router)
router.include_router(tool_catalog.router)
router.include_router(integrations.router)
router.include_router(integration_approvals.router)
router.include_router(egress.router)

"""Service-to-service routes under `/internal/*`.

Reachable only over `homeai-internal`; Caddy never routes `/internal/*`
(docs/PLATFORM.md §3). Everything here other than `health`, `jwks`, and the
future `auth/verify` will require a service bearer token.
"""

from fastapi import APIRouter

from app.api.internal import health, jwks

router = APIRouter(prefix="/internal")
router.include_router(health.router)
router.include_router(jwks.router)

"""Service-to-service routes under `/internal/*`.

Reachable only over `homeai-internal`; Caddy never routes `/internal/*`
(docs/PLATFORM.md §3). Everything here other than `health`, `jwks`, and
`auth/verify` will require a service bearer token.
"""

from fastapi import APIRouter

from app.api.internal import auth, health, jwks

router = APIRouter(prefix="/internal")
router.include_router(health.router)
router.include_router(jwks.router)
router.include_router(auth.router)

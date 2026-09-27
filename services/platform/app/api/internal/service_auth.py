"""Service-to-service bearer auth for `/internal/*` (docs/PLATFORM.md §4).

Each internal service presents its own secret from `.env`; an endpoint lists
which services may call it. An unset secret never matches, so a missing
`PLATFORM_*_TOKEN` fails closed.
"""

from __future__ import annotations

import hmac
from collections.abc import Awaitable, Callable

from fastapi import Request

from app.core.config import Settings
from app.core.errors import Unauthorized
from app.core.principal import bearer_token

AGENT = "agent"
EXEC = "exec"


def _secret(settings: Settings, service: str) -> str:
    return {AGENT: settings.platform_agent_token, EXEC: settings.platform_exec_token}[service]


def require_service(*services: str) -> Callable[[Request], Awaitable[str]]:
    """Dependency: the caller's bearer is the secret of one of `services`; returns its name."""

    async def dependency(request: Request) -> str:
        presented = bearer_token(request)
        if presented is not None:
            settings: Settings = request.app.state.settings
            for service in services:
                secret = _secret(settings, service)
                if secret and hmac.compare_digest(presented.encode(), secret.encode()):
                    return service
        raise Unauthorized("unauthenticated")

    return dependency

"""FastAPI application factory for code-exec-manager.

`create_app()` builds the ASGI app; the module-level `app` object below is
what the Dockerfile's `uv run uvicorn app.main:app` command serves.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import docker
from fastapi import FastAPI

from app.api import router
from app.core.config import Settings
from app.sessions import SessionManager


def create_app(settings: Settings | None = None, docker_client_override: Any | None = None) -> FastAPI:
    """Build the FastAPI app.

    `settings` lets tests inject config without touching real env vars /
    `.env.` - same convention as agent-server's own `create_app`.

    `docker_client_override` is the test escape hatch: when provided, the
    lifespan uses it directly and never calls `docker.from_env()` (which
    requires a real `/var/run/docker.sock`). When omitted - the production
    path, including the module-level `app = create_app()` below - the
    lifespan opens a real client and closes it on shutdown.

    Neither `docker.from_env()` nor `SessionManager(...)` runs at import
    time (only inside `lifespan`), so importing this module never requires
    Docker to be present - only actually starting the app (via uvicorn or a
    test's `async with AsyncClient(...)`/`LifespanManager`) does.
    """
    settings = settings or Settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        client = docker_client_override or docker.from_env()
        app.state.session_manager = SessionManager(client, app.state.settings)
        try:
            yield
        finally:
            if docker_client_override is None:
                client.close()

    app = FastAPI(lifespan=lifespan)
    app.state.settings = settings
    app.include_router(router)
    return app


app = create_app()

"""FastAPI application factory for code-exec-manager.

`create_app()` builds the ASGI app; the module-level `app` object below is
what the Dockerfile's `uv run uvicorn app.main:app` command serves.
"""

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import docker
from fastapi import FastAPI

from app.api import router
from app.core.config import Settings
from app.reaper import reap_loop
from app.sessions import SessionManager

# Uvicorn only configures its OWN loggers ("uvicorn"/"uvicorn.error"/
# "uvicorn.access") - it never touches the root logger's level, which
# defaults to WARNING. Without this, every `logger.info(...)` in
# `app.sessions`/`app.reaper` (notably the reaper's one-line-per-removal
# log, M4-03 spec) would be silently dropped rather than reaching `docker
# compose logs` - confirmed by a real live-fire reaper run whose removal
# never appeared in the container's logs until this was added.
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")


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
        reaper_task = asyncio.create_task(reap_loop(app.state.session_manager))
        try:
            yield
        finally:
            reaper_task.cancel()
            try:
                await reaper_task
            except asyncio.CancelledError:
                pass
            if docker_client_override is None:
                client.close()

    app = FastAPI(lifespan=lifespan)
    app.state.settings = settings
    app.include_router(router)
    return app


app = create_app()

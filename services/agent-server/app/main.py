"""FastAPI application factory for the agent-server.

`create_app()` builds the ASGI app; the module-level `app` object below is
what the Dockerfile's `uv run uvicorn app.main:app` command serves.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api import health
from app.core.config import Settings


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the FastAPI app.

    `settings` lets tests inject config without touching real env vars /
    `.env`. When omitted, `Settings()` reads from the environment as usual.
    """
    settings = settings or Settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        # No startup/shutdown work yet — agent construction, DB pool, etc.
        # land in later tickets (M2-03, M3-01).
        yield

    app = FastAPI(lifespan=lifespan)
    app.state.settings = settings

    app.include_router(health.router, prefix="/api")

    return app


app = create_app()

"""FastAPI application factory for the agent-server.

`create_app()` builds the ASGI app; the module-level `app` object below is
what the Dockerfile's `uv run uvicorn app.main:app` command serves.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from langgraph.checkpoint.base import BaseCheckpointSaver

from app.agent.build import build_agent
from app.api import chat_ws, health
from app.core.config import Settings
from app.db.checkpointer import build_postgres_checkpointer


def create_app(
    settings: Settings | None = None,
    checkpointer_override: BaseCheckpointSaver | None = None,
) -> FastAPI:
    """Build the FastAPI app.

    `settings` lets tests inject config without touching real env vars /
    `.env`. When omitted, `Settings()` reads from the environment as usual.

    The agent is intentionally NOT built here: it's built inside `lifespan`,
    reading `app.state.settings` at startup time. This lets a test pass a
    fake-model `Settings` override into `create_app()` and have the agent
    constructed against *that* settings object once the lifespan runs,
    rather than against whatever `Settings()` would resolve to by then.

    `checkpointer_override` is the escape hatch for tests: when provided, the
    lifespan uses it directly and never attempts a real Postgres connection
    (tests pass `MemorySaver()` here so the whole suite stays fast with zero
    real-Postgres dependency). When omitted — the production path, including
    the module-level `app = create_app()` below — the lifespan builds a real
    `AsyncPostgresSaver`-backed checkpointer from `settings.postgres_dsn` and
    closes its connection pool on shutdown.
    """
    settings = settings or Settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        if checkpointer_override is not None:
            app.state.agent = build_agent(app.state.settings, checkpointer_override)
            yield
            return

        pg_checkpointer = await build_postgres_checkpointer(app.state.settings.postgres_dsn)
        try:
            app.state.agent = build_agent(app.state.settings, pg_checkpointer.saver)
            yield
        finally:
            await pg_checkpointer.close()

    app = FastAPI(lifespan=lifespan)
    app.state.settings = settings

    app.include_router(health.router, prefix="/api")
    # No prefix: the WS route's own path (`/ws/chat/{thread_id}`) must match
    # Caddy's `/ws/*` routing exactly (see `infra/caddy/Caddyfile`), not be
    # nested under `/api` like the REST routes above.
    app.include_router(chat_ws.router)

    return app


app = create_app()

"""FastAPI application factory for the agent-server.

`create_app()` builds the ASGI app; the module-level `app` object below is
what the Dockerfile's `uv run uvicorn app.main:app` command serves.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from langgraph.checkpoint.base import BaseCheckpointSaver

from app.agent.build import build_agent
from app.api import chat, chat_ws, files, health
from app.core.config import Settings
from app.db.checkpointer import build_postgres_checkpointer
from app.db.threads import InMemoryThreadStore, PgThreadStore, ThreadStore


def create_app(
    settings: Settings | None = None,
    checkpointer_override: BaseCheckpointSaver | None = None,
    thread_store_override: ThreadStore | None = None,
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

    `thread_store_override` (M3-02) mirrors `checkpointer_override` exactly,
    for the same reason: the real `PgThreadStore` needs the same Postgres
    pool the real checkpointer opens, which doesn't exist in the test path.
    When `checkpointer_override` is given but `thread_store_override` isn't,
    this defaults to `InMemoryThreadStore()` (rather than requiring every
    existing `checkpointer_override`-only test fixture to also start passing
    `thread_store_override`) — tests that specifically exercise `ThreadStore`
    behavior still pass their own `InMemoryThreadStore()` explicitly so its
    state is inspectable from the test.
    """
    settings = settings or Settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        if checkpointer_override is not None:
            app.state.checkpointer = checkpointer_override
            app.state.thread_store = thread_store_override or InMemoryThreadStore()
            app.state.agent = build_agent(app.state.settings, checkpointer_override)
            yield
            return

        pg_checkpointer = await build_postgres_checkpointer(app.state.settings.postgres_dsn)
        try:
            app.state.checkpointer = pg_checkpointer.saver
            app.state.thread_store = thread_store_override or PgThreadStore(pg_checkpointer.pool)
            app.state.agent = build_agent(app.state.settings, pg_checkpointer.saver)
            yield
        finally:
            await pg_checkpointer.close()

    app = FastAPI(lifespan=lifespan)
    app.state.settings = settings

    app.include_router(health.router, prefix="/api")
    app.include_router(chat.router, prefix="/api")
    app.include_router(files.router, prefix="/api")
    # No prefix: the WS route's own path (`/ws/chat/{thread_id}`) must match
    # Caddy's `/ws/*` routing exactly (see `infra/caddy/Caddyfile`), not be
    # nested under `/api` like the REST routes above.
    app.include_router(chat_ws.router)

    return app


app = create_app()

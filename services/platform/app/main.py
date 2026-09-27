"""FastAPI application factory for the platform service.

`create_app()` builds the ASGI app; the module-level `app` object below is
what the Dockerfile's `uvicorn app.main:app` command serves.
"""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from psycopg_pool import AsyncConnectionPool

from app.api import internal
from app.api.external import auth, platform
from app.core.config import Settings
from app.core.tokens import TokenService, load_or_create_signing_key
from app.db.migrate import run_migrations
from app.db.pool import open_pool

logger = logging.getLogger(__name__)


def create_app(
    settings: Settings | None = None,
    db_pool_override: AsyncConnectionPool | None = None,
    token_service_override: TokenService | None = None,
) -> FastAPI:
    """Build the FastAPI app.

    `settings` lets tests inject config without touching real env vars /
    `.env`; when omitted, `Settings()` reads the environment.

    Startup (in `lifespan`, so it reads `app.state.settings` at start time):
    open the Postgres pool, apply pending migrations, load or generate the
    signing key. A failure in any step fails startup; compose restarts it.

    `db_pool_override` is an already-open pool the caller owns (the app
    migrates it but never closes it). `token_service_override` skips key
    loading from `settings.keys_dir`.
    """
    settings = settings or Settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        s: Settings = app.state.settings
        pool = db_pool_override or await open_pool(s.database_dsn)
        try:
            async with pool.connection() as conn:
                applied = await run_migrations(conn)
            logger.info("migrations: %d applied", len(applied))

            tokens = token_service_override or TokenService(load_or_create_signing_key(s.keys_dir))
            logger.info("signing key kid=%s", tokens.kid)

            app.state.db_pool = pool
            app.state.tokens = tokens
            yield
        finally:
            if db_pool_override is None:
                await pool.close()

    app = FastAPI(lifespan=lifespan)
    app.state.settings = settings

    app.include_router(internal.router)
    app.include_router(auth.router)
    app.include_router(platform.router)

    return app


logging.basicConfig(level=logging.INFO, format="%(levelname)s:%(name)s: %(message)s")
app = create_app()

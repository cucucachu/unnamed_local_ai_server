"""FastAPI application factory for the platform service.

`create_app()` builds the ASGI app; the module-level `app` object below is
what the Dockerfile's `uvicorn app.main:app` command serves.
"""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from psycopg_pool import AsyncConnectionPool

from app.api import internal
from app.api.external import auth, platform
from app.core.bootstrap import Bootstrap
from app.core.config import Settings
from app.core.errors import Conflict, Forbidden, InvalidInput, NotFound, PlatformError, Unauthorized
from app.core.ratelimit import RateLimited, RateLimiter
from app.core.tokens import TokenService, load_or_create_signing_key
from app.db.migrate import run_migrations
from app.db.pool import open_pool

logger = logging.getLogger(__name__)

STATUS_BY_ERROR: dict[type[PlatformError], int] = {
    Unauthorized: 401,
    Forbidden: 403,
    NotFound: 404,
    Conflict: 409,
    InvalidInput: 422,
}


def _install_error_handlers(app: FastAPI) -> None:
    """Every error body is `{"detail": "<code>"}` (docs/ARCHITECTURE.md §3 "Platform API")."""

    @app.exception_handler(PlatformError)
    async def platform_error(request: Request, exc: PlatformError) -> JSONResponse:
        return JSONResponse({"detail": exc.code}, status_code=STATUS_BY_ERROR.get(type(exc), 400))

    @app.exception_handler(RateLimited)
    async def rate_limited(request: Request, exc: RateLimited) -> JSONResponse:
        return JSONResponse(
            {"detail": "rate_limited"},
            status_code=429,
            headers={"Retry-After": str(exc.retry_after_s)},
        )

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request: Request, exc: RequestValidationError) -> JSONResponse:
        # Only location + message: pydantic's `input` would echo passwords back.
        errors = [{"loc": list(e["loc"]), "msg": e["msg"]} for e in exc.errors()]
        return JSONResponse({"detail": "invalid_request", "errors": errors}, status_code=422)


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
    signing key, and (until the first admin exists) prepare the bootstrap
    setup code. A failure in any step fails startup; compose restarts it.

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

            bootstrap = Bootstrap(s.platform_data_dir)
            async with pool.connection() as conn:
                await bootstrap.prepare(conn)

            app.state.db_pool = pool
            app.state.tokens = tokens
            app.state.bootstrap = bootstrap
            app.state.limiter = RateLimiter(
                s.platform_auth_rate_limit, s.platform_auth_rate_window_s
            )
            yield
        finally:
            if db_pool_override is None:
                await pool.close()

    app = FastAPI(lifespan=lifespan)
    app.state.settings = settings
    _install_error_handlers(app)

    app.include_router(internal.router)
    app.include_router(auth.router)
    app.include_router(platform.router)

    return app


logging.basicConfig(level=logging.INFO, format="%(levelname)s:%(name)s: %(message)s")
app = create_app()

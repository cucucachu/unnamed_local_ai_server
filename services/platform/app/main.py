"""FastAPI application factory for the platform service.

`create_app()` builds the ASGI app; the module-level `app` object below is
what the Dockerfile's `uvicorn app.main:app` command serves.
"""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import anyio.to_thread
from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from psycopg_pool import AsyncConnectionPool

from app.api import internal
from app.api.external import auth, events, platform
from app.core import appbuild, legacy, spaces
from app.core.agentfs import AgentFsError
from app.core.appdata import AppData
from app.core.apphistory import HISTORY_DIR, AppHistory, HistoryError
from app.core.bootstrap import Bootstrap
from app.core.config import Settings
from app.core.errors import (
    Conflict,
    Forbidden,
    InvalidApp,
    InvalidInput,
    MigrationFailed,
    NotFound,
    PlatformError,
    ServerError,
    SqlFailed,
    Unauthorized,
    Unavailable,
    UnsupportedMedia,
)
from app.core.events import EventHub
from app.core.ratelimit import RateLimited, RateLimiter
from app.core.storage import SpaceStorage, StorageError
from app.core.tokens import TokenService, load_or_create_signing_key
from app.db.migrate import run_migrations
from app.db.pool import open_pool

logger = logging.getLogger(__name__)

STATUS_BY_ERROR: dict[type[PlatformError], int] = {
    Unauthorized: 401,
    Forbidden: 403,
    NotFound: 404,
    Conflict: 409,
    UnsupportedMedia: 415,
    InvalidInput: 422,
    ServerError: 500,
    Unavailable: 503,
}


def _install_error_handlers(app: FastAPI) -> None:
    """Every error body is `{"detail": "<code>"}` (docs/ARCHITECTURE.md §3 "Platform API")."""

    @app.exception_handler(PlatformError)
    async def platform_error(request: Request, exc: PlatformError) -> JSONResponse:
        return JSONResponse({"detail": exc.code}, status_code=STATUS_BY_ERROR.get(type(exc), 400))

    @app.exception_handler(InvalidApp)
    async def invalid_app(request: Request, exc: InvalidApp) -> JSONResponse:
        return JSONResponse({"detail": exc.code, "diagnostics": exc.diagnostics}, status_code=422)

    @app.exception_handler(SqlFailed)
    async def sql_failed(request: Request, exc: SqlFailed) -> JSONResponse:
        body = {"detail": exc.code, "message": exc.message}
        if exc.index is not None:
            body["index"] = exc.index
        return JSONResponse(body, status_code=503 if exc.code == "db_busy" else 422)

    @app.exception_handler(MigrationFailed)
    async def migration_failed(request: Request, exc: MigrationFailed) -> JSONResponse:
        body = {"detail": exc.code, "migration": jsonable_encoder(exc.migration)}
        return JSONResponse(body, status_code=422)

    @app.exception_handler(AgentFsError)
    async def agent_fs_error(request: Request, exc: AgentFsError) -> JSONResponse:
        """Agent file-tool failures also carry deepagents' own wording in `message`."""
        body = {"detail": exc.code, "message": exc.message, **exc.extra}
        return JSONResponse(body, status_code=422)

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


async def _prepare_builds(s: Settings) -> appbuild.Builds | None:
    """The app builds staging root, or None (builds answer 503) if it can't be set up."""
    builds = appbuild.Builds(s.platform_builds_dir)
    try:
        await anyio.to_thread.run_sync(builds.prepare)
    except OSError as exc:
        logger.error("builds: %s unusable, app builds disabled: %s", builds.root, exc)
        return None
    return builds


async def _prepare_history(s: Settings) -> AppHistory | None:
    """The app source history root, or None (history answers 503, builds commit nothing)."""
    history = AppHistory(s.platform_data_dir / HISTORY_DIR)
    try:
        await anyio.to_thread.run_sync(history.prepare)
    except (OSError, HistoryError) as exc:
        logger.error("history: %s unusable, app history disabled: %s", history.root, exc)
        return None
    return history


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
    signing key, give any user without one a personal space, reconcile every
    space's directory tree (per-space failures are logged, not fatal), empty
    the app builds staging root (unusable: logged, builds disabled), prepare
    the app history root (unusable or no git: logged, history disabled), and
    (until the first admin exists) prepare the bootstrap setup code, then
    run the legacy files migration if it's enabled (logged, never fatal). A
    failure in any other step fails startup; compose restarts it.

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

            storage = SpaceStorage(s.platform_spaces_dir)
            if not storage.root.is_dir():
                raise StorageError(f"{storage.root}: spaces root is not a directory")
            async with pool.connection() as conn:
                backfilled = await spaces.backfill_personal_spaces(conn, storage)
                space_dirs = await spaces.all_space_dirs(conn)
            ok, failed = storage.reconcile(space_dirs)
            logger.info(
                "spaces: %d personal spaces backfilled; storage reconciled %d ok, %d failed",
                backfilled, ok, failed,
            )  # fmt: skip

            bootstrap = Bootstrap(s.platform_data_dir)
            async with pool.connection() as conn:
                await bootstrap.prepare(conn)

            app.state.db_pool = pool
            app.state.storage = storage
            app.state.tokens = tokens
            app.state.bootstrap = bootstrap
            app.state.builds = await _prepare_builds(s)
            app.state.history = await _prepare_history(s)
            app.state.builder = appbuild.ExecManagerBuilder(
                s.exec_manager_url, s.platform_build_token, s.platform_build_timeout_s
            )
            app.state.events = EventHub()
            app.state.appdata = AppData(pool, storage, app.state.events)
            app.state.limiter = RateLimiter(
                s.platform_auth_rate_limit, s.platform_auth_rate_window_s
            )
            await legacy.maybe_migrate(app)
            yield
        finally:
            if hasattr(app.state, "appdata"):
                await app.state.appdata.aclose()
            if db_pool_override is None:
                await pool.close()

    app = FastAPI(lifespan=lifespan)
    app.state.settings = settings
    _install_error_handlers(app)

    app.include_router(internal.router)
    app.include_router(auth.router)
    app.include_router(platform.router)
    app.include_router(events.router)

    return app


logging.basicConfig(level=logging.INFO, format="%(levelname)s:%(name)s: %(message)s")
app = create_app()

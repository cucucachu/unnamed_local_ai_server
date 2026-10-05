"""FastAPI application factory for the agent-server.

`create_app()` builds the ASGI app; the module-level `app` object below is
what the Dockerfile's `uv run uvicorn app.main:app` command serves.
"""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import timedelta

from fastapi import Depends, FastAPI
from langgraph.checkpoint.base import BaseCheckpointSaver

from app.agent.build import build_agent
from app.agent.turn_runner import TurnRunner
from app.api import chat, chat_ws, health, routines
from app.api import settings as settings_api
from app.core.config import Settings
from app.core.delegation import DelegationClient, HttpDelegationClient
from app.core.identity import (
    IdentityVerifier,
    JwksIdentityVerifier,
    current_user,
    http_jwks_fetcher,
)
from app.core.orphans import OrphanAdopter, http_admin_lookup
from app.db.checkpointer import build_postgres_checkpointer
from app.db.routines import InMemoryRoutineStore, PgRoutineStore, RoutineStore
from app.db.settings import InMemorySettingsStore, PgSettingsStore, SettingsStore
from app.db.threads import InMemoryThreadStore, PgThreadStore, ThreadStore
from app.db.turn_stats import InMemoryTurnStatsStore, PgTurnStatsStore, TurnStatsStore
from app.routines.scheduler import RoutineScheduler

logger = logging.getLogger(__name__)


def create_app(
    settings: Settings | None = None,
    checkpointer_override: BaseCheckpointSaver | None = None,
    thread_store_override: ThreadStore | None = None,
    settings_store_override: SettingsStore | None = None,
    turn_stats_store_override: TurnStatsStore | None = None,
    routine_store_override: RoutineStore | None = None,
    identity_verifier_override: IdentityVerifier | None = None,
    delegation_client_override: DelegationClient | None = None,
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

    `settings_store_override` (M8-02) mirrors `thread_store_override`
    exactly, for the same reason and with the same `InMemorySettingsStore()`
    fallback when omitted alongside a `checkpointer_override`.

    `turn_stats_store_override` (M9-02) mirrors `settings_store_override`
    the same way: the real `PgTurnStatsStore` needs the Postgres pool, and
    tests that don't pass an override get `InMemoryTurnStatsStore()` when a
    `checkpointer_override` is in play.

    `routine_store_override` (M17-02): same again, defaulting to
    `InMemoryRoutineStore` over the in-memory thread store.

    `identity_verifier_override` (M10-04) replaces the JWKS-backed verifier
    of `X-HomeAI-Identity` (`app/core/identity.py`). Unlike the stores it
    has no test-mode default: tests pass a fake that yields a fixed user,
    or a `JwksIdentityVerifier` over a local key pair.

    `delegation_client_override` (M11-02) replaces the HTTP client that
    exchanges and refreshes delegation tokens (`app/core/delegation.py`);
    by default it talks to `settings.platform_url`.
    """
    settings = settings or Settings()

    def install_orphan_adopter(app: FastAPI) -> None:
        s: Settings = app.state.settings
        lookup = None
        if s.platform_agent_token:
            lookup = http_admin_lookup(s.platform_url, s.platform_agent_token)
        else:
            logger.warning(
                "PLATFORM_AGENT_TOKEN unset: ownerless threads stay unassigned"
                " and chat sockets close (no delegations)"
            )
        app.state.orphan_adopter = OrphanAdopter(
            lookup, app.state.thread_store, app.state.settings_store
        )

    @asynccontextmanager
    async def _running_turns(app: FastAPI) -> AsyncIterator[None]:
        """The turn runner and the routine scheduler (M17-04), stopped in that order."""
        settings = app.state.settings
        scheduler = RoutineScheduler(
            app.state,
            poll_s=settings.routines_poll_s,
            max_concurrent=settings.routines_max_concurrent,
            grace=timedelta(seconds=settings.routines_missed_grace_s),
            run_timeout_s=settings.routine_run_timeout_s,
            approval_ttl=timedelta(seconds=settings.routine_approval_ttl_s),
        )
        app.state.routine_scheduler = scheduler
        try:
            if settings.routines_scheduler_enabled:
                await scheduler.start()
            yield
        finally:
            await scheduler.stop()
            await app.state.turn_runner.shutdown()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        if checkpointer_override is not None:
            app.state.checkpointer = checkpointer_override
            app.state.thread_store = thread_store_override or InMemoryThreadStore()
            app.state.settings_store = settings_store_override or InMemorySettingsStore()
            app.state.turn_stats_store = turn_stats_store_override or InMemoryTurnStatsStore()
            app.state.routine_store = routine_store_override or InMemoryRoutineStore(
                app.state.thread_store
            )
            install_orphan_adopter(app)
            app.state.agent = build_agent(app.state.settings, checkpointer_override, app.state)
            app.state.turn_runner = TurnRunner(app.state)
            async with _running_turns(app):
                yield
            return

        pg_checkpointer = await build_postgres_checkpointer(app.state.settings.postgres_dsn)
        try:
            app.state.checkpointer = pg_checkpointer.saver
            app.state.thread_store = thread_store_override or PgThreadStore(pg_checkpointer.pool)
            app.state.settings_store = settings_store_override or PgSettingsStore(
                pg_checkpointer.pool
            )
            app.state.turn_stats_store = turn_stats_store_override or PgTurnStatsStore(
                pg_checkpointer.pool
            )
            app.state.routine_store = routine_store_override or PgRoutineStore(pg_checkpointer.pool)
            install_orphan_adopter(app)
            app.state.agent = build_agent(app.state.settings, pg_checkpointer.saver, app.state)
            app.state.turn_runner = TurnRunner(app.state)
            async with _running_turns(app):
                yield
        finally:
            await pg_checkpointer.close()

    app = FastAPI(lifespan=lifespan)
    app.state.settings = settings
    app.state.identity_verifier = identity_verifier_override or JwksIdentityVerifier(
        http_jwks_fetcher(f"{settings.platform_url}/internal/jwks")
    )
    app.state.delegation_client = delegation_client_override or HttpDelegationClient(
        settings.platform_url, settings.platform_agent_token
    )

    authenticated = [Depends(current_user)]
    app.include_router(health.router, prefix="/api")
    app.include_router(chat.router, prefix="/api", dependencies=authenticated)
    app.include_router(settings_api.router, prefix="/api", dependencies=authenticated)
    app.include_router(routines.router, prefix="/api", dependencies=authenticated)
    # No prefix: the WS route's own path (`/ws/chat/{thread_id}`) must match
    # Caddy's `/ws/*` routing exactly (see `infra/caddy/Caddyfile`), not be
    # nested under `/api` like the REST routes above. It authenticates itself
    # after accepting, so a failure can be reported as close code 4401.
    app.include_router(chat_ws.router)

    return app


# Root stays at WARNING: httpx logs every model request at INFO.
logging.basicConfig(level=logging.WARNING, format="%(levelname)s:%(name)s: %(message)s")
logging.getLogger("app").setLevel(logging.INFO)
app = create_app()

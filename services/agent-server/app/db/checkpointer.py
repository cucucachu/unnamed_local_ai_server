"""Postgres-backed LangGraph checkpointer setup (M3-01).

Introspection notes (installed versions: `psycopg_pool==3.3.1`,
`langgraph-checkpoint-postgres` as pinned in `pyproject.toml`):

- `psycopg_pool.AsyncConnectionPool.__init__` accepts an `open: bool | None`
  kwarg. Passing `open=True` (or leaving the default, which resolves to
  `True`) makes the constructor open the pool eagerly, which triggers
  `psycopg_pool.pool_async`'s own `RuntimeWarning` ("opening the async pool
  ... in the constructor is deprecated ... use `await pool.open()`, or use
  the pool as context manager") whenever there's already a running event
  loop — which is always true here, since this runs inside FastAPI's async
  lifespan. So this module passes `open=False` and calls `await pool.open()`
  explicitly right after construction.
- The real import path is `langgraph.checkpoint.postgres.aio.AsyncPostgresSaver`
  (not `langgraph.checkpoint.postgres.AsyncPostgresSaver` — that top-level
  module only re-exports the sync `PostgresSaver`). Its constructor takes a
  `conn: _ainternal.Conn` positional arg, where `Conn = AsyncConnection[DictRow]
  | AsyncConnectionPool[AsyncConnection[DictRow]]` — i.e. handing it the pool
  directly (`AsyncPostgresSaver(pool)`) is a supported, real code path, not a
  guess.
"""

from __future__ import annotations

from dataclasses import dataclass

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

# Our own metadata table, separate from the checkpointer's own
# `checkpoints`/`checkpoint_blobs`/`checkpoint_writes`/`checkpoint_migrations`
# tables (created by `AsyncPostgresSaver.setup()`, called before this DDL
# runs). The checkpointer stores graph *state*; this stores thread titles/
# ordering for a later ticket (M3-02) - out of scope here beyond the bare
# table existing.
#
# `gen_random_uuid()` confirmed (against a real running `postgres:17`
# container) to work out of the box, no `CREATE EXTENSION pgcrypto` needed -
# it's been a built-in `pgcrypto`-free core function since PG 13.
_THREADS_TABLE_DDL = """
CREATE TABLE IF NOT EXISTS threads (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    title TEXT NOT NULL DEFAULT 'New chat',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""

# `settings` (M8-02): one row per `SettingsDocument` field, see
# `app/db/settings.py` for the read/merge logic. Created here, next to
# `threads`, per the ticket's "same pattern as `app/db/threads.py`" spec.
_SETTINGS_TABLE_DDL = """
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value JSONB NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""


@dataclass
class PostgresCheckpointer:
    """Bundles the pool + saver so the app lifespan can tear both down cleanly.

    `saver` is what gets handed to `build_agent()`. `pool` is kept around
    purely so `close()` has something to close - callers shouldn't need to
    touch it directly otherwise.
    """

    pool: AsyncConnectionPool
    saver: AsyncPostgresSaver

    async def close(self) -> None:
        await self.pool.close()


async def build_postgres_checkpointer(dsn: str) -> PostgresCheckpointer:
    """Open a pool, build+migrate an `AsyncPostgresSaver`, and ensure `threads` exists.

    Called once at app startup (real Postgres path only - tests inject a
    `MemorySaver` via `create_app(checkpointer_override=...)` instead and
    never call this). `autocommit=True` + `prepare_threshold=0` +
    `row_factory=dict_row` on the pool's connections are required by
    `AsyncPostgresSaver` itself (it manages its own transactions internally
    and expects dict-shaped rows) - per `langgraph-checkpoint-postgres`'s own
    `from_conn_string` reference implementation, which opens connections with
    these exact same three kwargs.
    """
    pool = AsyncConnectionPool(
        dsn,
        open=False,
        kwargs={"autocommit": True, "prepare_threshold": 0, "row_factory": dict_row},
    )
    await pool.open()

    saver = AsyncPostgresSaver(pool)
    await saver.setup()

    async with pool.connection() as conn:
        await conn.execute(_THREADS_TABLE_DDL)
        await conn.execute(_SETTINGS_TABLE_DDL)

    return PostgresCheckpointer(pool=pool, saver=saver)

"""Async Postgres connection pool for the `homeai_platform` database."""

from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool


async def open_pool(dsn: str, *, timeout_s: float = 30.0) -> AsyncConnectionPool:
    """Open a pool and wait until it holds a live connection.

    Connections are autocommit: code that needs atomicity opens an explicit
    `async with conn.transaction():` block, so a transaction's extent is
    always visible at the call site. `open=False` + `await pool.open()`
    avoids psycopg_pool's deprecation warning for opening in the constructor
    under a running loop (same as agent-server's `app/db/checkpointer.py`).
    """
    pool = AsyncConnectionPool(
        dsn,
        min_size=1,
        max_size=10,
        open=False,
        kwargs={"autocommit": True, "row_factory": dict_row},
    )
    await pool.open(wait=True, timeout=timeout_s)
    return pool

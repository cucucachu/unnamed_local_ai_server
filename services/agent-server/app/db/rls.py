"""Postgres row-level security for agent-server's tables (#194).

Every user-data table (`threads`, `user_settings`, the LangGraph checkpoint
tables, `turn_stats`) has `FORCE ROW LEVEL SECURITY` and a policy keyed on
the session setting `app.user_id`: a row is visible, and may be written, only
while that setting names the user who owns it. `threads` and `user_settings`
carry the owner themselves; the thread-keyed tables match
`thread_id` against the ids of the caller's own threads. The pre-M10-04
global `settings` table has no policy at all, so it's invisible. `agent`
owns these tables, which is why the policies must be *forced*: an owner
otherwise bypasses them.

The setting comes from `bind_user`, which the request/WebSocket
authentication calls with the verified caller. `RlsConnectionPool` copies it
onto each connection as it is checked out (session-level: the pool's
connections are autocommit, so there's no enclosing transaction for `SET
LOCAL`, and LangGraph's saver runs its own statements) and clears it with a
`reset` hook before the connection goes back into the pool. A connection
checked out with no user bound carries no setting and sees no rows.

The one deliberate bypass is `system_transaction`: a transaction that `SET
LOCAL ROLE`s to `agent_rls_bypass` (a BYPASSRLS role that `db-init` creates
and lets `agent` switch to) — used only to hand pre-Stage-3 data to the
bootstrap admin. Startup DDL and LangGraph's migrations are unaffected: RLS
filters rows, not DDL.

This guards against agent-server code paths that forget an ownership check;
it doesn't stop a compromised agent-server, which holds the `agent`
credential, owns the tables, and can set `app.user_id` to anyone.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from contextvars import ContextVar

from psycopg import AsyncConnection
from psycopg_pool import AsyncConnectionPool

BYPASS_ROLE = "agent_rls_bypass"

_user_id: ContextVar[str | None] = ContextVar("rls_user_id", default=None)

# A scalar subquery, so it's evaluated once per statement (an InitPlan)
# rather than once per row, and can drive an index scan even in the generic
# plans a prepared statement gets.
_CURRENT_USER = "(SELECT nullif(current_setting('app.user_id', true), '')::uuid)"
_OWN_THREAD_IDS = f"SELECT id::text FROM threads WHERE owner_user_id = {_CURRENT_USER}"

# Table -> the condition a row must meet (both to be seen and to be written).
_POLICIES = {
    "threads": f"owner_user_id = {_CURRENT_USER}",
    "user_settings": f"user_id = {_CURRENT_USER}",
    "checkpoints": f"thread_id IN ({_OWN_THREAD_IDS})",
    "checkpoint_blobs": f"thread_id IN ({_OWN_THREAD_IDS})",
    "checkpoint_writes": f"thread_id IN ({_OWN_THREAD_IDS})",
    "turn_stats": f"thread_id IN ({_OWN_THREAD_IDS})",
}
# RLS on, no policy: nobody but the bypass role sees it.
_NO_POLICY = ("settings",)

POLICY_NAME = "owner_only"


def rls_ddl() -> list[str]:
    """Idempotent statements that (re)apply RLS; run in one transaction at startup."""
    statements = []
    for table, condition in _POLICIES.items():
        statements += [
            f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY",
            f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY",
            f"DROP POLICY IF EXISTS {POLICY_NAME} ON {table}",
            f"CREATE POLICY {POLICY_NAME} ON {table} USING ({condition}) WITH CHECK ({condition})",
        ]
    for table in _NO_POLICY:
        statements += [
            f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY",
            f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY",
        ]
    # Exactly what `adopt_orphans` / `adopt_legacy` run as the bypass role.
    statements += [
        f"GRANT SELECT, UPDATE ON threads TO {BYPASS_ROLE}",
        f"GRANT SELECT, INSERT ON user_settings TO {BYPASS_ROLE}",
        f"GRANT SELECT, DELETE ON settings TO {BYPASS_ROLE}",
    ]
    return statements


def bind_user(user_id: str) -> None:
    """Scope this task's (and its child tasks') database access to `user_id`.

    Called once per request/WebSocket, right after authentication. Tasks are
    per request/socket, so nothing needs unbinding.
    """
    _user_id.set(user_id)


def bound_user() -> str | None:
    return _user_id.get()


async def _clear_user(conn: AsyncConnection) -> None:
    await conn.execute("RESET app.user_id")


class RlsConnectionPool(AsyncConnectionPool):
    """An `AsyncConnectionPool` whose checked-out connections carry `app.user_id`.

    Still an `AsyncConnectionPool`, so `AsyncPostgresSaver` accepts it
    unchanged (it `isinstance`-checks and calls `connection()`, which calls
    `getconn`).
    """

    def __init__(self, *args, **kwargs) -> None:
        if "reset" in kwargs:
            raise TypeError("RlsConnectionPool installs its own reset hook")
        super().__init__(*args, reset=_clear_user, **kwargs)

    async def getconn(self, timeout: float | None = None) -> AsyncConnection:
        conn = await super().getconn(timeout)
        user_id = _user_id.get()
        if user_id is None:
            return conn
        try:
            await conn.execute("SELECT set_config('app.user_id', %s, false)", (user_id,))
        except BaseException:
            await self.putconn(conn)
            raise
        return conn


@asynccontextmanager
async def system_transaction(pool: AsyncConnectionPool) -> AsyncIterator[AsyncConnection]:
    """A transaction that sees and writes every row, as `agent_rls_bypass`."""
    async with pool.connection() as conn, conn.transaction():
        await conn.execute(f"SET LOCAL ROLE {BYPASS_ROLE}")
        yield conn

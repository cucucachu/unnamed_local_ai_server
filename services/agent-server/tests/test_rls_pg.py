"""Row-level security on agent-server's tables (#194), against a real Postgres.

The throwaway server from `pg_server` is initialized by `db-init.sh`, and
agent-server's own startup DDL runs as `agent` (the tables' owner), exactly
like the live stack. Every check goes through the same `RlsConnectionPool`
the app uses, including raw SQL on its connections: that's the path a
missing ownership check in agent-server would take.
"""

from __future__ import annotations

import asyncio
import contextvars
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable

import psycopg
import pytest
from httpx import ASGITransport, AsyncClient
from psycopg.rows import dict_row

from app.agent.build import build_agent
from app.core.identity import Identity
from app.db import rls
from app.db.checkpointer import PostgresCheckpointer, build_postgres_checkpointer
from app.db.settings import PgSettingsStore
from app.db.threads import PgThreadStore
from app.db.turn_stats import PgTurnStatsStore, TurnStat
from app.main import create_app
from tests.conftest import PgServer
from tests.fake_identity import FixedIdentityVerifier
from tests.fake_model.scripting import FakeModel, TextTurn

pytestmark = pytest.mark.integration

USER_TABLES = ("threads", "routines", "user_settings", "checkpoints", "checkpoint_blobs",
               "checkpoint_writes", "turn_stats")  # fmt: skip
ALL_TABLES = (*USER_TABLES, "settings")
THREAD_KEYED = ("checkpoints", "checkpoint_blobs", "checkpoint_writes", "turn_stats")


async def as_user[T](user_id: str | None, fn: Callable[[], Awaitable[T]]) -> T:
    """Run `fn` in a task of its own with `user_id` bound (or nobody)."""

    async def run() -> T:
        if user_id is not None:
            rls.bind_user(user_id)
        return await fn()

    return await asyncio.create_task(run(), context=contextvars.Context())


def superuser(pg_server: PgServer) -> psycopg.Connection:
    return psycopg.connect(pg_server.super_dsn, autocommit=True)


@pytest.fixture
async def pg(pg_server: PgServer) -> AsyncIterator[PostgresCheckpointer]:
    checkpointer = await build_postgres_checkpointer(pg_server.agent_dsn)
    try:
        yield checkpointer
    finally:
        await checkpointer.close()


async def raw(pool, sql: str, params: tuple = ()) -> list[dict]:
    async with pool.connection() as conn:
        cur = await conn.execute(sql, params)
        return await cur.fetchall() if cur.description else []


async def count(pool, table: str, thread_id: str | None = None) -> int:
    where, params = ("WHERE thread_id = %s", (thread_id,)) if thread_id else ("", ())
    rows = await raw(pool, f"SELECT count(*) AS n FROM {table} {where}", params)
    return rows[0]["n"]


async def a_turn(pg: PostgresCheckpointer, fake_model: FakeModel, owner: str) -> str:
    """A real agent turn in a new thread of `owner`'s: writes checkpoints, blobs and writes."""
    record = await as_user(owner, lambda: PgThreadStore(pg.pool).create(owner, "mine"))
    fake_model.queue(TextTurn("a reply"))
    agent = build_agent(fake_model.settings(), pg.saver)
    config = {"configurable": {"thread_id": record.id}}
    await as_user(
        owner,
        lambda: agent.ainvoke({"messages": [{"role": "user", "content": "hello"}]}, config),
    )
    stat = TurnStat(record.id, "final-1", "completed", 5, record.created_at)
    await as_user(owner, lambda: PgTurnStatsStore(pg.pool).upsert(stat))
    await as_user(
        owner, lambda: PgSettingsStore(pg.pool).update_document(owner, {"hitl_enabled": False})
    )
    return record.id


def test_startup_ddl_forces_rls_on_every_table(pg, pg_server) -> None:
    with superuser(pg_server) as conn:
        rows = conn.execute(
            "SELECT relname, relrowsecurity, relforcerowsecurity, relowner::regrole::text "
            "FROM pg_class WHERE relnamespace = 'public'::regnamespace AND relname = ANY(%s)",
            (list(ALL_TABLES),),
        ).fetchall()
        assert sorted(rows) == sorted((t, True, True, "agent") for t in ALL_TABLES)
        policies = conn.execute(
            "SELECT tablename FROM pg_policies WHERE schemaname = 'public'"
        ).fetchall()
        assert sorted(p for (p,) in policies) == sorted(USER_TABLES)
        # `checkpoint_migrations` holds no user data and stays readable.
        forced = conn.execute(
            "SELECT relrowsecurity FROM pg_class WHERE relname = 'checkpoint_migrations'"
        ).fetchone()
        assert forced == (False,)


async def test_startup_ddl_is_idempotent(pg, pg_server) -> None:
    again = await build_postgres_checkpointer(pg_server.agent_dsn)
    await again.close()
    with superuser(pg_server) as conn:
        (n,) = conn.execute(
            "SELECT count(*) FROM pg_policies WHERE schemaname = 'public'"
        ).fetchone()
    assert n == len(USER_TABLES)


async def test_other_users_rows_are_invisible_even_to_raw_sql(pg, pg_server, fake_model) -> None:
    alice, bob = str(uuid.uuid4()), str(uuid.uuid4())
    thread = await a_turn(pg, fake_model, alice)
    with superuser(pg_server) as conn:
        for table in THREAD_KEYED:
            (n,) = conn.execute(
                f"SELECT count(*) FROM {table} WHERE thread_id = %s", (thread,)
            ).fetchone()
            assert n > 0, table

    for table in THREAD_KEYED:
        assert await as_user(alice, lambda t=table: count(pg.pool, t, thread)) > 0, table
        assert await as_user(bob, lambda t=table: count(pg.pool, t, thread)) == 0, table
    assert await as_user(bob, lambda: count(pg.pool, "threads")) == 0
    assert (
        await as_user(
            bob, lambda: raw(pg.pool, "SELECT * FROM user_settings WHERE user_id = %s", (alice,))
        )
        == []
    )

    config = {"configurable": {"thread_id": thread}}
    assert await as_user(alice, lambda: pg.saver.aget_tuple(config)) is not None
    assert await as_user(bob, lambda: pg.saver.aget_tuple(config)) is None
    assert await as_user(bob, lambda: PgThreadStore(pg.pool).get(thread, alice)) is None
    assert await as_user(bob, lambda: PgTurnStatsStore(pg.pool).list_for_thread(thread)) == []
    bob_view = await as_user(bob, lambda: PgSettingsStore(pg.pool).get_document(alice))
    assert bob_view.hitl_enabled is True  # defaults: alice's row is invisible


async def test_other_users_rows_cant_be_changed_or_deleted(pg, pg_server, fake_model) -> None:
    alice, bob = str(uuid.uuid4()), str(uuid.uuid4())
    thread = await a_turn(pg, fake_model, alice)

    def snapshot() -> dict[str, int]:
        with superuser(pg_server) as conn:
            counts = {
                t: conn.execute(
                    f"SELECT count(*) FROM {t} WHERE thread_id = %s", (thread,)
                ).fetchone()[0]
                for t in THREAD_KEYED
            }
            counts["threads"] = conn.execute(
                "SELECT count(*) FROM threads WHERE id = %s", (thread,)
            ).fetchone()[0]
            counts["user_settings"] = conn.execute(
                "SELECT count(*) FROM user_settings WHERE user_id = %s", (alice,)
            ).fetchone()[0]
            return counts

    before = snapshot()

    async def bobs_attempts() -> None:
        async with pg.pool.connection() as conn:
            for sql, params in [
                ("UPDATE threads SET title = 'pwned' WHERE id = %s", (thread,)),
                ("DELETE FROM threads WHERE id = %s", (thread,)),
                ("UPDATE user_settings SET value = 'true' WHERE user_id = %s", (alice,)),
                ("DELETE FROM user_settings WHERE user_id = %s", (alice,)),
                *[(f"DELETE FROM {t} WHERE thread_id = %s", (thread,)) for t in THREAD_KEYED],
                ("UPDATE checkpoints SET metadata = '{}' WHERE thread_id = %s", (thread,)),
            ]:
                cur = await conn.execute(sql, params)
                assert cur.rowcount == 0, sql
        await pg.saver.adelete_thread(thread)
        assert await PgThreadStore(pg.pool).delete(thread, alice) is False

    await as_user(bob, bobs_attempts)
    assert snapshot() == before
    with superuser(pg_server) as conn:
        (title,) = conn.execute("SELECT title FROM threads WHERE id = %s", (thread,)).fetchone()
    assert title == "mine"


@pytest.mark.parametrize(
    "sql",
    [
        "INSERT INTO threads (title, owner_user_id) VALUES ('x', %(victim)s)",
        "UPDATE threads SET owner_user_id = %(victim)s WHERE owner_user_id = %(me)s",
        "INSERT INTO user_settings (user_id, key, value) VALUES (%(victim)s, 'k', 'true')",
        "INSERT INTO checkpoints (thread_id, checkpoint_id, checkpoint)  VALUES (%(thread)s, 'forged', '{}')",
        "INSERT INTO checkpoint_blobs (thread_id, channel, version, type)  VALUES (%(thread)s, 'messages', '99', 'empty')",
        "INSERT INTO checkpoint_writes (thread_id, checkpoint_id, task_id, idx, channel, blob)  VALUES (%(thread)s, 'c', 't', 0, 'messages', '')",
        "INSERT INTO turn_stats VALUES (%(thread)s, 'm', 'completed', 1, now())",
    ],
)  # fmt: skip
async def test_writing_into_another_users_rows_is_refused(pg, fake_model, sql) -> None:
    alice, bob = str(uuid.uuid4()), str(uuid.uuid4())
    thread = await a_turn(pg, fake_model, alice)
    await as_user(bob, lambda: PgThreadStore(pg.pool).create(bob, "bob's"))
    args = {"victim": alice, "me": bob, "thread": thread}

    with pytest.raises(psycopg.errors.InsufficientPrivilege, match="row-level security"):
        await as_user(bob, lambda: raw(pg.pool, sql, args))


async def test_no_bound_user_sees_and_writes_nothing(pg, fake_model) -> None:
    await a_turn(pg, fake_model, str(uuid.uuid4()))
    for table in ALL_TABLES:
        assert await as_user(None, lambda t=table: count(pg.pool, t)) == 0, table
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        await as_user(
            None, lambda: raw(pg.pool, "INSERT INTO threads (title) VALUES ('ownerless')")
        )


async def test_returned_connection_carries_no_user(pg_server, fake_model) -> None:
    """One connection, checked out by alice, returned, checked out again."""
    alice, bob = str(uuid.uuid4()), str(uuid.uuid4())
    single = await build_postgres_checkpointer(pg_server.agent_dsn)
    await single.close()  # DDL done; now a pool of exactly one connection
    pool = rls.RlsConnectionPool(
        pg_server.agent_dsn, open=False, min_size=1, max_size=1,
        kwargs={"autocommit": True, "prepare_threshold": 0, "row_factory": dict_row},
    )  # fmt: skip
    await pool.open()
    try:

        async def peek() -> tuple[int, str | None, int]:
            async with pool.connection() as conn:
                cur = await conn.execute(
                    "SELECT pg_backend_pid() AS pid, current_setting('app.user_id', true) AS uid, "
                    "(SELECT count(*) FROM threads) AS n"
                )
                row = await cur.fetchone()
            return row["pid"], row["uid"], row["n"]

        await as_user(alice, lambda: PgThreadStore(pool).create(alice, "alice's"))
        pid, uid, n = await as_user(alice, peek)
        assert (uid, n) == (alice, 1)

        # The pool's reset runs in a background worker; wait until it's back.
        for _ in range(100):
            if pool.get_stats().get("pool_available", 0) == 1:
                break
            await asyncio.sleep(0.01)

        again_pid, uid, n = await as_user(None, peek)
        assert again_pid == pid
        assert uid in (None, "")
        assert n == 0

        bob_pid, uid, n = await as_user(bob, peek)
        assert (bob_pid, uid, n) == (pid, bob, 0)
    finally:
        await pool.close()


async def test_concurrent_users_on_a_shared_pool_never_see_each_other(pg, fake_model) -> None:
    users = [str(uuid.uuid4()) for _ in range(6)]
    threads = {u: await a_turn(pg, fake_model, u) for u in users}

    async def look(user: str) -> None:
        for _ in range(25):
            rows = await raw(pg.pool, "SELECT DISTINCT thread_id FROM checkpoints")
            assert [r["thread_id"] for r in rows] == [threads[user]]
            await asyncio.sleep(0)

    await asyncio.gather(*(as_user(u, lambda u=u: look(u)) for u in users * 3))


async def test_orphan_and_legacy_handover_through_the_bypass_role(
    pg, pg_server, fake_model
) -> None:
    admin = str(uuid.uuid4())
    thread = await a_turn(pg, fake_model, str(uuid.uuid4()))
    with superuser(pg_server) as conn:
        conn.execute("UPDATE threads SET owner_user_id = NULL WHERE id = %s", (thread,))
        conn.execute("DELETE FROM settings")
        conn.execute("INSERT INTO settings (key, value) VALUES ('thinking_enabled', 'true')")

    store, settings = PgThreadStore(pg.pool), PgSettingsStore(pg.pool)
    config = {"configurable": {"thread_id": thread}}
    assert await as_user(admin, lambda: store.get(thread, admin)) is None
    assert await as_user(admin, lambda: count(pg.pool, "settings")) == 0

    # Without the bypass the same UPDATE sees no ownerless rows.
    cur_rows = await as_user(
        admin,
        lambda: raw(
            pg.pool,
            "UPDATE threads SET owner_user_id = %s WHERE owner_user_id IS NULL RETURNING id",
            (admin,),
        ),
    )
    assert cur_rows == []

    assert await as_user(admin, lambda: store.adopt_orphans(admin)) >= 1
    assert await as_user(admin, lambda: settings.adopt_legacy(admin)) == 1
    assert (await as_user(admin, lambda: store.get(thread, admin))).title == "mine"
    assert await as_user(admin, lambda: pg.saver.aget_tuple(config)) is not None
    assert (await as_user(admin, lambda: settings.get_document(admin))).thinking_enabled is True
    with superuser(pg_server) as conn:
        assert conn.execute("SELECT count(*) FROM settings").fetchone() == (0,)
        assert conn.execute(
            "SELECT count(*) FROM threads WHERE owner_user_id IS NULL"
        ).fetchone() == (0,)


async def test_bypass_role_is_scoped_to_its_transaction(pg) -> None:
    async def role_after_bypass() -> tuple[str, str]:
        async with rls.system_transaction(pg.pool) as conn:
            inside = (await (await conn.execute("SELECT current_user AS u")).fetchone())["u"]
        rows = await raw(pg.pool, "SELECT current_user AS u")
        return inside, rows[0]["u"]

    assert await as_user(None, role_after_bypass) == ("agent_rls_bypass", "agent")


async def test_delete_thread_removes_every_checkpoint(pg, pg_server, fake_model) -> None:
    owner = str(uuid.uuid4())
    thread = await a_turn(pg, fake_model, owner)
    app = create_app(
        fake_model.settings(),
        checkpointer_override=pg.saver,
        thread_store_override=PgThreadStore(pg.pool),
        settings_store_override=PgSettingsStore(pg.pool),
        turn_stats_store_override=PgTurnStatsStore(pg.pool),
        identity_verifier_override=FixedIdentityVerifier(Identity(owner, None, "member")),
    )
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:

            async def delete() -> int:
                return (await client.delete(f"/api/threads/{thread}")).status_code

            assert await as_user(None, delete) == 204

    with superuser(pg_server) as conn:
        for table in THREAD_KEYED:
            (n,) = conn.execute(
                f"SELECT count(*) FROM {table} WHERE thread_id = %s", (thread,)
            ).fetchone()
            assert n == 0, table
        assert conn.execute("SELECT count(*) FROM threads WHERE id = %s", (thread,)).fetchone() == (
            0,
        )

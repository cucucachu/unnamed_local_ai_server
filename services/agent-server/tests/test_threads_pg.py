"""Integration test for `PgThreadStore` (M3-02) against a real Postgres.

Runs against the throwaway Postgres from `tests/conftest.py`'s `pg_server`
(initialized by `infra/postgres/db-init.sh`, connected to as `agent` like
the live stack), so row-level security applies: each test binds the user it
acts as, and arranges or cleans up rows as the superuser. Skipped without
the `docker` CLI.
"""

from __future__ import annotations

import uuid

import psycopg
import pytest

from app.db import rls
from app.db.checkpointer import build_postgres_checkpointer
from app.db.threads import DEFAULT_TITLE, PgThreadStore
from tests.conftest import PgServer

pytestmark = pytest.mark.integration


async def test_pg_thread_store_round_trip(pg_server: PgServer) -> None:
    pg = await build_postgres_checkpointer(pg_server.agent_dsn)
    store = PgThreadStore(pg.pool)
    owner, other = str(uuid.uuid4()), str(uuid.uuid4())
    rls.bind_user(owner)
    try:
        # create() + get(): default title, DTO fields all round-trip.
        record = await store.create(owner, None)
        assert record.title == DEFAULT_TITLE
        assert record.created_at == record.updated_at
        assert record.owner_user_id == owner

        fetched = await store.get(record.id, owner)
        assert fetched == record

        # create() with an explicit title.
        titled = await store.create(owner, "Trip planning")
        assert titled.title == "Trip planning"

        # list_for_owner(): ordered by updated_at desc - `titled` was created
        # after `record`, so it sorts first.
        listing = await store.list_for_owner(owner)
        assert [r.id for r in listing] == [titled.id, record.id]

        # touch(): bumps updated_at.
        before_touch = await store.get(record.id, owner)
        await store.touch(record.id)
        after_touch = await store.get(record.id, owner)
        assert after_touch.updated_at > before_touch.updated_at

        # set_title_if_new(): only applies while title is still the default.
        await store.set_title_if_new(record.id, "Should not apply")
        unaffected = await store.get(record.id, owner)
        assert unaffected.title == "Should not apply"  # was still DEFAULT_TITLE at call time

        await store.set_title_if_new(record.id, "Should also not apply")
        still_unaffected = await store.get(record.id, owner)
        assert still_unaffected.title == "Should not apply"  # no-op: title is no longer default

        # M19-02: rename() sets any title, owner only.
        renamed = await store.rename(record.id, owner, "Renamed")
        assert renamed is not None and renamed.title == "Renamed"
        assert await store.rename(record.id, other, "Theirs") is None
        assert (await store.get(record.id, owner)).title == "Renamed"
        assert await store.rename("not-a-uuid", owner, "x") is None

        # Another user sees none of it and can't delete it.
        assert await store.list_for_owner(other) == []
        assert await store.get(record.id, other) is None
        assert await store.delete(record.id, other) is False
        assert await store.get(record.id, owner) is not None

        # M17-10: a turn's end sets the flags; only the owner's read clears unread.
        await store.turn_ended(record.id, awaiting_approval=True, unread=True)
        flagged = await store.get(record.id, owner)
        assert (flagged.awaiting_approval, flagged.unread) == (True, True)
        await store.turn_started(record.id)
        await store.turn_ended(record.id, awaiting_approval=False, unread=False)
        flagged = await store.get(record.id, owner)
        assert (flagged.awaiting_approval, flagged.unread) == (False, True)
        rls.bind_user(other)
        assert await store.mark_read(record.id, other) is False
        rls.bind_user(owner)
        assert await store.mark_read(record.id, other) is False
        assert (await store.get(record.id, owner)).unread is True
        assert await store.mark_read(record.id, owner) is True
        assert (await store.get(record.id, owner)).unread is False

        # delete(): removes the row; a second call reports nothing deleted.
        assert await store.delete(record.id, owner) is True
        assert await store.get(record.id, owner) is None
        assert await store.delete(record.id, owner) is False

        # Non-UUID thread ids: every method no-ops/returns None rather than
        # raising `invalid input syntax for type uuid` (see `PgThreadStore`'s
        # docstring for why - WS thread ids aren't always real UUIDs).
        non_uuid = "not-a-uuid-thread-id"
        assert await store.get(non_uuid, owner) is None
        assert await store.delete(non_uuid, owner) is False
        await store.set_title_if_new(non_uuid, "x")
        await store.touch(non_uuid)
        await store.turn_started(non_uuid)
        await store.turn_ended(non_uuid, awaiting_approval=True, unread=True)
        assert await store.mark_read(non_uuid, owner) is False
    finally:
        with psycopg.connect(pg_server.super_dsn, autocommit=True) as conn:
            conn.execute("DELETE FROM threads WHERE owner_user_id IN (%s, %s)", (owner, other))
        await pg.close()


async def test_pg_adopt_orphans(pg_server: PgServer) -> None:
    pg = await build_postgres_checkpointer(pg_server.agent_dsn)
    store = PgThreadStore(pg.pool)
    admin = str(uuid.uuid4())
    rls.bind_user(admin)
    try:
        with psycopg.connect(pg_server.super_dsn, autocommit=True) as conn:
            (orphan_id,) = conn.execute(
                "INSERT INTO threads (title) VALUES ('legacy') RETURNING id::text"
            ).fetchone()

        assert await store.get(orphan_id, admin) is None
        assert await store.adopt_orphans(admin) >= 1
        adopted = await store.get(orphan_id, admin)
        assert adopted is not None and adopted.title == "legacy"
        assert await store.adopt_orphans(admin) == 0
    finally:
        with psycopg.connect(pg_server.super_dsn, autocommit=True) as conn:
            conn.execute("DELETE FROM threads WHERE owner_user_id = %s", (admin,))
        await pg.close()

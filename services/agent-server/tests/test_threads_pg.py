"""Integration test for `PgThreadStore` (M3-02) against a real Postgres.

Mirrors `tests/test_checkpointer_pg.py`'s skip-marker pattern exactly:
requires a real Postgres reachable via the `TEST_PG_DSN` env var, SKIPPED
(not failed) otherwise.

Run it:

    TEST_PG_DSN=postgresql://user:pass@host:5432/db uv run pytest -m integration

or, against the real compose Postgres (`TEST_PG_DSN` already set there via
`docker-compose.yml`'s `agent-server.environment` block):

    docker compose exec agent-server uv run pytest -m integration
"""

from __future__ import annotations

import os
import uuid

import pytest

from app.db.checkpointer import build_postgres_checkpointer
from app.db.threads import DEFAULT_TITLE, PgThreadStore

TEST_PG_DSN = os.environ.get("TEST_PG_DSN")

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not TEST_PG_DSN, reason="TEST_PG_DSN not set - no real Postgres to test against"
    ),
]


async def test_pg_thread_store_round_trip() -> None:
    pg = await build_postgres_checkpointer(TEST_PG_DSN)
    store = PgThreadStore(pg.pool)
    created_ids: list[str] = []
    try:
        # create() + get(): default title, DTO fields all round-trip.
        record = await store.create(None)
        created_ids.append(record.id)
        assert record.title == DEFAULT_TITLE
        assert record.created_at == record.updated_at

        fetched = await store.get(record.id)
        assert fetched == record

        # create() with an explicit title.
        titled = await store.create("Trip planning")
        created_ids.append(titled.id)
        assert titled.title == "Trip planning"

        # list_all(): ordered by updated_at desc - `titled` was created after
        # `record`, so it sorts first.
        listing = await store.list_all()
        listed_ids = [r.id for r in listing]
        assert listed_ids.index(titled.id) < listed_ids.index(record.id)

        # touch(): bumps updated_at.
        before_touch = await store.get(record.id)
        await store.touch(record.id)
        after_touch = await store.get(record.id)
        assert after_touch.updated_at > before_touch.updated_at

        # set_title_if_new(): only applies while title is still the default.
        await store.set_title_if_new(record.id, "Should not apply")
        unaffected = await store.get(record.id)
        assert unaffected.title == "Should not apply"  # was still DEFAULT_TITLE at call time

        await store.set_title_if_new(record.id, "Should also not apply")
        still_unaffected = await store.get(record.id)
        assert still_unaffected.title == "Should not apply"  # no-op: title is no longer default

        # ensure_exists(): no-ops for a row that already exists (title
        # untouched), and creates a fresh row (with a caller-chosen UUID id)
        # for one that doesn't.
        await store.ensure_exists(record.id)
        reconfirmed = await store.get(record.id)
        assert reconfirmed.title == "Should not apply"

        fresh_id = str(uuid.uuid4())
        created_ids.append(fresh_id)
        assert await store.get(fresh_id) is None
        await store.ensure_exists(fresh_id)
        fresh_record = await store.get(fresh_id)
        assert fresh_record is not None
        assert fresh_record.title == DEFAULT_TITLE

        # delete(): removes the row; idempotent on a second call.
        await store.delete(record.id)
        assert await store.get(record.id) is None
        await store.delete(record.id)  # no error on an already-gone row

        # Non-UUID thread ids: every method no-ops/returns None rather than
        # raising `invalid input syntax for type uuid` (see `PgThreadStore`'s
        # docstring for why - WS thread ids aren't always real UUIDs).
        non_uuid = "not-a-uuid-thread-id"
        assert await store.get(non_uuid) is None
        await store.delete(non_uuid)
        await store.ensure_exists(non_uuid)
        await store.set_title_if_new(non_uuid, "x")
        await store.touch(non_uuid)
    finally:
        # Leave the DB clean regardless of assertion outcome.
        for thread_id in created_ids:
            await store.delete(thread_id)
        await pg.close()

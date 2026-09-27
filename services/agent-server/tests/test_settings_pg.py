"""Integration test for `PgSettingsStore` (M8-02) against a real Postgres.

Mirrors `tests/test_threads_pg.py`'s skip-marker pattern exactly: requires a
real Postgres reachable via the `TEST_PG_DSN` env var, SKIPPED (not failed)
otherwise.

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
from app.db.settings import PgSettingsStore

TEST_PG_DSN = os.environ.get("TEST_PG_DSN")

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not TEST_PG_DSN, reason="TEST_PG_DSN not set - no real Postgres to test against"
    ),
]


async def test_pg_settings_store_round_trip() -> None:
    pg = await build_postgres_checkpointer(TEST_PG_DSN)
    store = PgSettingsStore(pg.pool)
    user, other = str(uuid.uuid4()), str(uuid.uuid4())
    try:
        # get_document(): defaults applied when nothing is stored.
        defaults = await store.get_document(user)
        assert defaults.hitl_enabled is True
        assert defaults.thinking_enabled is False
        assert defaults.edit_mode_default == "truncate"

        # update_document(): partial merge, persisted for real.
        merged = await store.update_document(user, {"hitl_enabled": False})
        assert merged.hitl_enabled is False
        assert merged.thinking_enabled is False  # untouched, still default

        # A fresh store instance (same pool) sees the persisted change —
        # confirms this is real Postgres state, not in-process caching.
        reloaded = await PgSettingsStore(pg.pool).get_document(user)
        assert reloaded.hitl_enabled is False

        # A second partial update only touches its own field.
        merged2 = await store.update_document(
            user, {"thinking_enabled": True, "edit_mode_default": "fork"}
        )
        assert merged2.hitl_enabled is False  # preserved
        assert merged2.thinking_enabled is True
        assert merged2.edit_mode_default == "fork"

        # Per user: someone else still has the defaults.
        assert await store.get_document(other) == defaults
    finally:
        async with pg.pool.connection() as conn:
            await conn.execute("DELETE FROM user_settings WHERE user_id IN (%s, %s)", (user, other))
        await pg.close()


async def test_pg_adopt_legacy_settings() -> None:
    pg = await build_postgres_checkpointer(TEST_PG_DSN)
    store = PgSettingsStore(pg.pool)
    admin = str(uuid.uuid4())
    try:
        async with pg.pool.connection() as conn:
            cur = await conn.execute("SELECT count(*) AS n FROM settings")
            if (await cur.fetchone())["n"]:
                pytest.skip("database has real legacy settings; adopting them would move them")
            await conn.execute(
                "INSERT INTO settings (key, value) VALUES "
                "('hitl_enabled', 'false'), ('edit_mode_default', '\"fork\"')"
            )
        await store.update_document(admin, {"edit_mode_default": "truncate"})

        assert await store.adopt_legacy(admin) == 1
        document = await store.get_document(admin)
        assert document.hitl_enabled is False
        assert document.edit_mode_default == "truncate"
        async with pg.pool.connection() as conn:
            cur = await conn.execute("SELECT count(*) AS n FROM settings")
            assert (await cur.fetchone())["n"] == 0
        assert await store.adopt_legacy(admin) == 0
    finally:
        async with pg.pool.connection() as conn:
            await conn.execute("DELETE FROM user_settings WHERE user_id = %s", (admin,))
        await pg.close()

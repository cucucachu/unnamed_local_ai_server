"""Integration test for `PgSettingsStore` (M8-02) against a real Postgres.

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
from app.db.settings import PgSettingsStore
from tests.conftest import PgServer

pytestmark = pytest.mark.integration


async def test_pg_settings_store_round_trip(pg_server: PgServer) -> None:
    pg = await build_postgres_checkpointer(pg_server.agent_dsn)
    store = PgSettingsStore(pg.pool)
    user, other = str(uuid.uuid4()), str(uuid.uuid4())
    rls.bind_user(user)
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
        with psycopg.connect(pg_server.super_dsn, autocommit=True) as conn:
            conn.execute("DELETE FROM user_settings WHERE user_id IN (%s, %s)", (user, other))
        await pg.close()


async def test_pg_adopt_legacy_settings(pg_server: PgServer) -> None:
    pg = await build_postgres_checkpointer(pg_server.agent_dsn)
    store = PgSettingsStore(pg.pool)
    admin = str(uuid.uuid4())
    rls.bind_user(admin)
    try:
        with psycopg.connect(pg_server.super_dsn, autocommit=True) as conn:
            conn.execute("DELETE FROM settings")
            conn.execute(
                "INSERT INTO settings (key, value) VALUES "
                "('hitl_enabled', 'false'), ('edit_mode_default', '\"fork\"')"
            )
        await store.update_document(admin, {"edit_mode_default": "truncate"})

        assert await store.adopt_legacy(admin) == 1
        document = await store.get_document(admin)
        assert document.hitl_enabled is False
        assert document.edit_mode_default == "truncate"
        with psycopg.connect(pg_server.super_dsn) as conn:
            assert conn.execute("SELECT count(*) FROM settings").fetchone() == (0,)
        assert await store.adopt_legacy(admin) == 0
    finally:
        with psycopg.connect(pg_server.super_dsn, autocommit=True) as conn:
            conn.execute("DELETE FROM user_settings WHERE user_id = %s", (admin,))
        await pg.close()

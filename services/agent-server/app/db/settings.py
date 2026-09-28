"""`SettingsStore`: the raw-SQL data layer for per-user settings (M8-02, M10-04).

Mirrors `app/db/threads.py`'s pattern exactly: one `Protocol` with two
implementations behind `app.main.create_app`'s `settings_store_override`
param (which mirrors `checkpointer_override`/`thread_store_override`'s
existing test-injection pattern).

- `PgSettingsStore`: raw SQL against the `user_settings` table (DDL below, run
  alongside `threads`'s own DDL at startup — see `build_postgres_checkpointer`
  in `app/db/checkpointer.py`). This is the module-level `app = create_app()`
  production default.
- `InMemorySettingsStore`: dict-backed, used by the unit test suite (no real
  Postgres) and as `create_app()`'s test-mode default when
  `checkpointer_override` is given but no `settings_store_override` is.

Each user has one logical document (the `SettingsDocument` pydantic model
below), persisted as individual `(user_id, key, value jsonb)` rows so a
future ticket can add more keys without a schema migration — each pydantic
field maps to one row keyed by its field name. Reading applies pydantic
defaults for any key not yet present in storage (a user's first-ever `GET`,
or a key introduced by a later ticket that predates any row for it).

Before M10-04 there was one global document in `settings` (`key` primary
key). `adopt_legacy` moves those rows to the bootstrap admin (keys the admin
already has win) and empties the old table, so it runs at most once.
"""

from __future__ import annotations

from typing import Literal, Protocol

from psycopg.types.json import Json
from psycopg_pool import AsyncConnectionPool
from pydantic import BaseModel

from app.db.rls import system_transaction


class SettingsDocument(BaseModel):
    """The whole settings document. Defaults apply to any key missing from storage.

    `hitl_enabled` defaults to **on**: the feature exists to guard file
    writes and code execution, so opting in to unattended mode is the
    explicit choice (per the ticket).
    """

    hitl_enabled: bool = True
    thinking_enabled: bool = False
    edit_mode_default: Literal["truncate", "fork"] = "truncate"


class SettingsStore(Protocol):
    """Everything `app/api/settings.py` and `app/api/chat_ws.py` need.

    `get_document`/`update_document` operate on one user's whole document
    (rather than per-key get/set) since that's the shape both REST endpoints
    need — `GET` always returns the full merged-with-defaults document, and
    `PUT` accepts a partial one and returns the full merged result.
    """

    async def get_document(self, user_id: str) -> SettingsDocument: ...

    async def update_document(self, user_id: str, partial: dict) -> SettingsDocument: ...

    async def adopt_legacy(self, user_id: str) -> int: ...


LEGACY_SETTINGS_TABLE_DDL = """
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value JSONB NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""

USER_SETTINGS_TABLE_DDL = """
CREATE TABLE IF NOT EXISTS user_settings (
    user_id UUID NOT NULL,
    key TEXT NOT NULL,
    value JSONB NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, key)
);
"""


class PgSettingsStore:
    """Raw-SQL `SettingsStore` against `user_settings` (DDL above, run from
    `app/db/checkpointer.py::build_postgres_checkpointer` alongside
    `threads`'s own DDL).

    Each `SettingsDocument` field is stored as its own `(user_id, key,
    value)` row (`value` a JSONB-encoded scalar/literal) rather than one big
    JSON blob — so `update_document` can `INSERT ... ON CONFLICT DO UPDATE`
    one row per changed field, and a future new settings field just adds a
    new row the first time it's written, no migration needed.
    """

    def __init__(self, pool: AsyncConnectionPool) -> None:
        self._pool = pool

    async def get_document(self, user_id: str) -> SettingsDocument:
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                "SELECT key, value FROM user_settings WHERE user_id = %s", (user_id,)
            )
            rows = await cur.fetchall()
        stored = {row["key"]: row["value"] for row in rows}
        # Defaults fill in any key not yet present in storage — pydantic
        # itself applies `SettingsDocument`'s field defaults for whatever
        # `stored` doesn't cover.
        return SettingsDocument.model_validate(stored)

    async def update_document(self, user_id: str, partial: dict) -> SettingsDocument:
        current = await self.get_document(user_id)
        merged = current.model_copy(update=partial)

        async with self._pool.connection() as conn:
            for key in partial:
                value = getattr(merged, key)
                await conn.execute(
                    """
                    INSERT INTO user_settings (user_id, key, value, updated_at)
                    VALUES (%s, %s, %s, now())
                    ON CONFLICT (user_id, key) DO UPDATE
                        SET value = EXCLUDED.value, updated_at = EXCLUDED.updated_at
                    """,
                    (user_id, key, Json(value)),
                )
        return merged

    async def adopt_legacy(self, user_id: str) -> int:
        # `settings` has no policy: only the bypass role can read it.
        async with system_transaction(self._pool) as conn:
            cur = await conn.execute(
                """
                INSERT INTO user_settings (user_id, key, value, updated_at)
                SELECT %s, key, value, updated_at FROM settings
                ON CONFLICT (user_id, key) DO NOTHING
                """,
                (user_id,),
            )
            moved = cur.rowcount
            await conn.execute("DELETE FROM settings")
        return moved


class InMemorySettingsStore:
    """Dict-backed `SettingsStore` for the unit test suite (no real Postgres)
    and `create_app()`'s test-mode default.

    Stores only the keys explicitly written via `update_document` (mirroring
    `PgSettingsStore`'s per-key-row storage) — `get_document` always applies
    `SettingsDocument`'s defaults for anything not yet stored, exactly like
    the Postgres-backed implementation. `legacy` plays the pre-M10-04 global
    document for `adopt_legacy`.
    """

    def __init__(self, legacy: dict | None = None) -> None:
        self._stored: dict[str, dict] = {}
        self.legacy: dict = dict(legacy or {})

    async def get_document(self, user_id: str) -> SettingsDocument:
        return SettingsDocument.model_validate(self._stored.get(user_id, {}))

    async def update_document(self, user_id: str, partial: dict) -> SettingsDocument:
        current = await self.get_document(user_id)
        merged = current.model_copy(update=partial)
        stored = self._stored.setdefault(user_id, {})
        for key in partial:
            stored[key] = getattr(merged, key)
        return merged

    async def adopt_legacy(self, user_id: str) -> int:
        stored = self._stored.setdefault(user_id, {})
        moved = {k: v for k, v in self.legacy.items() if k not in stored}
        stored.update(moved)
        self.legacy.clear()
        return len(moved)

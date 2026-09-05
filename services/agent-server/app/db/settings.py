"""`SettingsStore`: the raw-SQL data layer for the `settings` table (M8-02).

Mirrors `app/db/threads.py`'s pattern exactly: one `Protocol` with two
implementations behind `app.main.create_app`'s `settings_store_override`
param (which mirrors `checkpointer_override`/`thread_store_override`'s
existing test-injection pattern).

- `PgSettingsStore`: raw SQL against the `settings` table (DDL below, run
  alongside `threads`'s own DDL at startup — see `build_postgres_checkpointer`
  in `app/db/checkpointer.py`). This is the module-level `app = create_app()`
  production default.
- `InMemorySettingsStore`: dict-backed, used by the unit test suite (no real
  Postgres) and as `create_app()`'s test-mode default when
  `checkpointer_override` is given but no `settings_store_override` is.

Unlike `threads` (one row per thread), `settings` is a single-document key/
value table: one logical document (the `SettingsDocument` pydantic model
below), persisted as individual `(key, value jsonb)` rows so a future ticket
can add more keys without a schema migration — each pydantic field maps to
one row keyed by its field name. Reading applies pydantic defaults for any
key not yet present in storage (first-ever `GET`, or a key introduced by a
later ticket that predates any row for it).
"""

from __future__ import annotations

from typing import Literal, Protocol

from psycopg.types.json import Json
from psycopg_pool import AsyncConnectionPool
from pydantic import BaseModel


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
    """Everything `app/api/settings.py` needs.

    `get_document`/`update_document` operate on the whole document (rather
    than per-key get/set) since that's the shape both REST endpoints need —
    `GET` always returns the full merged-with-defaults document, and `PUT`
    accepts a partial one and returns the full merged result.
    """

    async def get_document(self) -> SettingsDocument: ...

    async def update_document(self, partial: dict) -> SettingsDocument: ...


_SETTINGS_TABLE_DDL = """
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value JSONB NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""


class PgSettingsStore:
    """Raw-SQL `SettingsStore` against the `settings` table (DDL above, run
    from `app/db/checkpointer.py::build_postgres_checkpointer` alongside
    `threads`'s own DDL).

    Each `SettingsDocument` field is stored as its own `(key, value)` row
    (`value` a JSONB-encoded scalar/literal) rather than one big JSON blob
    under a single key — so `update_document` can `INSERT ... ON CONFLICT
    DO UPDATE` one row per changed field with a single multi-row statement,
    and a future new settings field just adds a new row the first time it's
    written, no migration needed.
    """

    def __init__(self, pool: AsyncConnectionPool) -> None:
        self._pool = pool

    async def get_document(self) -> SettingsDocument:
        async with self._pool.connection() as conn:
            cur = await conn.execute("SELECT key, value FROM settings")
            rows = await cur.fetchall()
        stored = {row["key"]: row["value"] for row in rows}
        # Defaults fill in any key not yet present in storage — pydantic
        # itself applies `SettingsDocument`'s field defaults for whatever
        # `stored` doesn't cover.
        return SettingsDocument.model_validate(stored)

    async def update_document(self, partial: dict) -> SettingsDocument:
        current = await self.get_document()
        merged = current.model_copy(update=partial)

        async with self._pool.connection() as conn:
            for key in partial:
                value = getattr(merged, key)
                await conn.execute(
                    """
                    INSERT INTO settings (key, value, updated_at)
                    VALUES (%s, %s, now())
                    ON CONFLICT (key) DO UPDATE
                        SET value = EXCLUDED.value, updated_at = EXCLUDED.updated_at
                    """,
                    (key, Json(value)),
                )
        return merged


class InMemorySettingsStore:
    """Dict-backed `SettingsStore` for the unit test suite (no real Postgres)
    and `create_app()`'s test-mode default.

    Stores only the keys explicitly written via `update_document` (mirroring
    `PgSettingsStore`'s per-key-row storage) — `get_document` always applies
    `SettingsDocument`'s defaults for anything not yet stored, exactly like
    the Postgres-backed implementation.
    """

    def __init__(self) -> None:
        self._stored: dict = {}

    async def get_document(self) -> SettingsDocument:
        return SettingsDocument.model_validate(self._stored)

    async def update_document(self, partial: dict) -> SettingsDocument:
        current = await self.get_document()
        merged = current.model_copy(update=partial)
        for key in partial:
            self._stored[key] = getattr(merged, key)
        return merged

"""`ThreadStore`: the raw-SQL data layer for the `threads` table (M3-02).

Two implementations behind one `Protocol` (see `app.main.create_app`'s
`thread_store_override` param, which mirrors `checkpointer_override`'s
existing test-injection pattern exactly):

- `PgThreadStore`: raw SQL (four queries total — no ORM, per the ticket)
  against the real Postgres pool built in `app/db/checkpointer.py`. This is
  the module-level `app = create_app()` production default.
- `InMemoryThreadStore`: dict-backed, used by the unit test suite (which has
  no real Postgres, per `tests/test_checkpointer_pg.py`'s own docstring) and
  as `create_app()`'s test-mode default when `checkpointer_override` is
  given but no `thread_store_override` is.

Introspection note (`psycopg==3.3.4`, `psycopg_pool==3.3.1`): a pool
connection's `row_factory=dict_row` (set on the pool in
`app/db/checkpointer.py`, reused here — see `PgThreadStore.__init__`) means
`await conn.execute(...)` returns an `AsyncCursor` whose `fetchone`/
`fetchall` yield plain `dict`s keyed by column name, so no manual
row-tuple-to-dict mapping is needed. `uuid` columns come back as real
`uuid.UUID` objects (not `str`) and `timestamptz` columns as tz-aware
`datetime.datetime` objects — confirmed by reading `psycopg`'s built-in
`uuid`/`timestamptz` adapters (`psycopg/types/uuid.py`,
`psycopg/types/datetime.py`), which register exactly those Python types for
those OIDs.
"""

from __future__ import annotations

import itertools
import uuid
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Protocol

from psycopg_pool import AsyncConnectionPool

DEFAULT_TITLE = "New chat"


@dataclass(frozen=True)
class ThreadRecord:
    """One `threads` row, decoupled from both the DB row shape and the REST DTO."""

    id: str
    title: str
    created_at: datetime
    updated_at: datetime


class ThreadStore(Protocol):
    """Everything `app/api/chat.py` (REST) and `app/api/chat_ws.py` (WS side-effects) need.

    The REST-facing methods (`create`/`list_all`/`get`/`delete`) map directly
    onto the Conventions & Contracts §5 endpoints. The WS-facing methods
    (`ensure_exists`/`set_title_if_new`/`touch`) back this ticket's
    `chat_ws.py` touch-ups and have no REST route of their own.
    """

    async def create(self, title: str | None) -> ThreadRecord: ...

    async def list_all(self) -> list[ThreadRecord]: ...

    async def get(self, thread_id: str) -> ThreadRecord | None: ...

    async def delete(self, thread_id: str) -> None: ...

    async def ensure_exists(self, thread_id: str) -> None: ...

    async def set_title_if_new(self, thread_id: str, title: str) -> None: ...

    async def touch(self, thread_id: str) -> None: ...


def _is_valid_uuid(value: str) -> bool:
    try:
        uuid.UUID(value)
    except ValueError:
        return False
    return True


class PgThreadStore:
    """Raw-SQL `ThreadStore` against the `threads` table (DDL in `app/db/checkpointer.py`).

    Deliberate deviation, documented per the ticket's process requirements:
    `threads.id` is a real Postgres `UUID` column, but `WS
    /ws/chat/{thread_id}` has always accepted ANY string as a thread id
    (pre-existing §6 contract — e.g. `scripts/ws_smoke.py`'s default
    `smoke-1`, `gate_m2.sh`'s `gate-m2`, this repo's own WS unit tests'
    `plain-thread` etc.), and Postgres raises `invalid input syntax for type
    uuid` if such a non-UUID string is bound against a `UUID` column/param.
    So every method taking a caller-supplied `thread_id` guards with
    `_is_valid_uuid` first and no-ops/returns-`None` (rather than letting the
    query raise) for non-UUID ids, instead of altering the already-shipped
    (M3-01) `id UUID` column type. This keeps `chat_ws.py`'s new
    `ensure_exists`/`set_title_if_new`/`touch` calls from ever crashing a
    live chat turn for one of these legacy/manual non-UUID thread ids - the
    `threads` table bookkeeping this ticket adds is simply a best-effort
    side channel for them, entirely independent of the LangGraph checkpoint
    tables (keyed on `thread_id` as plain `TEXT`), which is what actually
    carries chat history/memory and is completely unaffected either way.
    Threads created through the new REST API (the only way to get a `title`/
    `updated_at`-tracked row going forward) always get a real `id UUID`, so
    this guard never fires for the ticket's own primary path.
    """

    _SELECT_COLUMNS = "id, title, created_at, updated_at"

    def __init__(self, pool: AsyncConnectionPool) -> None:
        self._pool = pool

    async def create(self, title: str | None) -> ThreadRecord:
        row_title = title if title else DEFAULT_TITLE
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                f"INSERT INTO threads (title) VALUES (%s) RETURNING {self._SELECT_COLUMNS}",
                (row_title,),
            )
            row = await cur.fetchone()
        return _record_from_row(row)

    async def list_all(self) -> list[ThreadRecord]:
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                f"SELECT {self._SELECT_COLUMNS} FROM threads ORDER BY updated_at DESC"
            )
            rows = await cur.fetchall()
        return [_record_from_row(row) for row in rows]

    async def get(self, thread_id: str) -> ThreadRecord | None:
        if not _is_valid_uuid(thread_id):
            return None
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                f"SELECT {self._SELECT_COLUMNS} FROM threads WHERE id = %s",
                (thread_id,),
            )
            row = await cur.fetchone()
        return _record_from_row(row) if row is not None else None

    async def delete(self, thread_id: str) -> None:
        if not _is_valid_uuid(thread_id):
            return
        async with self._pool.connection() as conn:
            await conn.execute("DELETE FROM threads WHERE id = %s", (thread_id,))

    async def ensure_exists(self, thread_id: str) -> None:
        if not _is_valid_uuid(thread_id):
            return
        async with self._pool.connection() as conn:
            await conn.execute(
                "INSERT INTO threads (id) VALUES (%s) ON CONFLICT (id) DO NOTHING",
                (thread_id,),
            )

    async def set_title_if_new(self, thread_id: str, title: str) -> None:
        if not _is_valid_uuid(thread_id):
            return
        async with self._pool.connection() as conn:
            await conn.execute(
                "UPDATE threads SET title = %s WHERE id = %s AND title = %s",
                (title, thread_id, DEFAULT_TITLE),
            )

    async def touch(self, thread_id: str) -> None:
        if not _is_valid_uuid(thread_id):
            return
        async with self._pool.connection() as conn:
            await conn.execute(
                "UPDATE threads SET updated_at = now() WHERE id = %s", (thread_id,)
            )


def _record_from_row(row: dict) -> ThreadRecord:
    return ThreadRecord(
        id=str(row["id"]),
        title=row["title"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


class InMemoryThreadStore:
    """Dict-backed `ThreadStore` for the unit test suite (no real Postgres) and
    `create_app()`'s test-mode default.

    Unlike `PgThreadStore`, this accepts ANY string as a thread id (no UUID
    column to satisfy) - matching the existing WS test suite's plain string
    thread ids (`plain-thread`, `tool-thread`, etc, see `test_chat_ws.py`)
    exactly, with zero special-casing needed in tests.

    Ordering for `list_all` is tracked via a monotonically increasing
    counter bumped on every `create`/`touch` (the two operations that change
    `updated_at`), rather than sorting by the `updated_at` timestamp values
    themselves - real `datetime.now()` calls issued back-to-back within a
    single test can land in the same microsecond on a fast machine, which
    would make timestamp-sorted order nondeterministic/flaky. The counter
    has no real-world meaning beyond recency ordering; the returned
    `ThreadRecord.updated_at` is still a real wall-clock value.
    """

    def __init__(self) -> None:
        self._rows: dict[str, ThreadRecord] = {}
        self._recency: dict[str, int] = {}
        self._counter = itertools.count()

    def _bump_recency(self, thread_id: str) -> None:
        self._recency[thread_id] = next(self._counter)

    async def create(self, title: str | None) -> ThreadRecord:
        thread_id = str(uuid.uuid4())
        now = datetime.now(UTC)
        record = ThreadRecord(
            id=thread_id, title=title if title else DEFAULT_TITLE, created_at=now, updated_at=now
        )
        self._rows[thread_id] = record
        self._bump_recency(thread_id)
        return record

    async def list_all(self) -> list[ThreadRecord]:
        return sorted(
            self._rows.values(), key=lambda r: self._recency.get(r.id, 0), reverse=True
        )

    async def get(self, thread_id: str) -> ThreadRecord | None:
        return self._rows.get(thread_id)

    async def delete(self, thread_id: str) -> None:
        self._rows.pop(thread_id, None)
        self._recency.pop(thread_id, None)

    async def ensure_exists(self, thread_id: str) -> None:
        if thread_id in self._rows:
            return
        now = datetime.now(UTC)
        self._rows[thread_id] = ThreadRecord(
            id=thread_id, title=DEFAULT_TITLE, created_at=now, updated_at=now
        )
        self._bump_recency(thread_id)

    async def set_title_if_new(self, thread_id: str, title: str) -> None:
        record = self._rows.get(thread_id)
        if record is not None and record.title == DEFAULT_TITLE:
            self._rows[thread_id] = replace(record, title=title)

    async def touch(self, thread_id: str) -> None:
        record = self._rows.get(thread_id)
        if record is None:
            return
        self._rows[thread_id] = replace(record, updated_at=datetime.now(UTC))
        self._bump_recency(thread_id)

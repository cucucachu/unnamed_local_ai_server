"""`ThreadStore`: the raw-SQL data layer for the `threads` table (M3-02).

Two implementations behind one `Protocol` (see `app.main.create_app`'s
`thread_store_override` param, which mirrors `checkpointer_override`'s
existing test-injection pattern exactly):

- `PgThreadStore`: raw SQL (no ORM, per the ticket) against the real
  Postgres pool built in `app/db/checkpointer.py`. This is
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

Ownership (M10-04, docs/PLATFORM.md §6): every thread belongs to one user
(`owner_user_id`, the platform user id from the verified identity). Every
caller-facing lookup takes the caller's id and treats another user's thread
exactly like a missing one. `NULL` owners are pre-Stage-3 threads, invisible
to everyone until `adopt_orphans` hands them to the bootstrap admin.
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
    # M8-05: tip the history/WS paths should read. `None` = chronological latest.
    active_checkpoint_id: str | None = None
    owner_user_id: str | None = None


class ThreadStore(Protocol):
    """Everything `app/api/chat.py` (REST) and `app/api/chat_ws.py` (WS side-effects) need.

    The owner-scoped methods (`create`/`list_for_owner`/`get`/`delete`) map
    onto the Conventions & Contracts §5 endpoints; `get`/`delete` return
    `None`/`False` for another user's thread. The rest (`set_title_if_new`/
    `touch`/`set_active_checkpoint_id`) take an id the caller has already
    resolved through `get`. `adopt_orphans` gives every ownerless thread to
    `owner_user_id` and returns how many it moved.
    """

    async def create(self, owner_user_id: str, title: str | None) -> ThreadRecord: ...

    async def list_for_owner(self, owner_user_id: str) -> list[ThreadRecord]: ...

    async def get(self, thread_id: str, owner_user_id: str) -> ThreadRecord | None: ...

    async def delete(self, thread_id: str, owner_user_id: str) -> bool: ...

    async def adopt_orphans(self, owner_user_id: str) -> int: ...

    async def set_title_if_new(self, thread_id: str, title: str) -> None: ...

    async def touch(self, thread_id: str) -> None: ...

    async def set_active_checkpoint_id(
        self, thread_id: str, checkpoint_id: str | None
    ) -> None: ...


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
    (M3-01) `id UUID` column type. Since M10-04 a WS turn needs an owned
    `threads` row, so such ids simply don't exist for anyone; their old
    checkpoints stay in the LangGraph tables, unreachable.
    """

    _SELECT_COLUMNS = "id, title, created_at, updated_at, active_checkpoint_id, owner_user_id"

    def __init__(self, pool: AsyncConnectionPool) -> None:
        self._pool = pool

    async def create(self, owner_user_id: str, title: str | None) -> ThreadRecord:
        row_title = title if title else DEFAULT_TITLE
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                f"INSERT INTO threads (title, owner_user_id) VALUES (%s, %s) "
                f"RETURNING {self._SELECT_COLUMNS}",
                (row_title, owner_user_id),
            )
            row = await cur.fetchone()
        return _record_from_row(row)

    async def list_for_owner(self, owner_user_id: str) -> list[ThreadRecord]:
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                f"SELECT {self._SELECT_COLUMNS} FROM threads WHERE owner_user_id = %s "
                "ORDER BY updated_at DESC",
                (owner_user_id,),
            )
            rows = await cur.fetchall()
        return [_record_from_row(row) for row in rows]

    async def get(self, thread_id: str, owner_user_id: str) -> ThreadRecord | None:
        if not _is_valid_uuid(thread_id):
            return None
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                f"SELECT {self._SELECT_COLUMNS} FROM threads WHERE id = %s AND owner_user_id = %s",
                (thread_id, owner_user_id),
            )
            row = await cur.fetchone()
        return _record_from_row(row) if row is not None else None

    async def delete(self, thread_id: str, owner_user_id: str) -> bool:
        if not _is_valid_uuid(thread_id):
            return False
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                "DELETE FROM threads WHERE id = %s AND owner_user_id = %s",
                (thread_id, owner_user_id),
            )
        return cur.rowcount > 0

    async def adopt_orphans(self, owner_user_id: str) -> int:
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                "UPDATE threads SET owner_user_id = %s WHERE owner_user_id IS NULL",
                (owner_user_id,),
            )
        return cur.rowcount

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

    async def set_active_checkpoint_id(
        self, thread_id: str, checkpoint_id: str | None
    ) -> None:
        if not _is_valid_uuid(thread_id):
            return
        async with self._pool.connection() as conn:
            await conn.execute(
                "UPDATE threads SET active_checkpoint_id = %s WHERE id = %s",
                (checkpoint_id, thread_id),
            )


def _record_from_row(row: dict) -> ThreadRecord:
    return ThreadRecord(
        id=str(row["id"]),
        title=row["title"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        active_checkpoint_id=row.get("active_checkpoint_id"),
        owner_user_id=str(row["owner_user_id"]) if row.get("owner_user_id") else None,
    )


class InMemoryThreadStore:
    """Dict-backed `ThreadStore` for the unit test suite (no real Postgres) and
    `create_app()`'s test-mode default.

    Unlike `PgThreadStore`, this accepts ANY string as a thread id (no UUID
    column to satisfy); `insert` seeds a record with a chosen id (and owner,
    or none for an orphan) for tests.

    Ordering for `list_for_owner` is tracked via a monotonically increasing
    counter bumped on every `insert`/`touch` (the two operations that change
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

    def insert(
        self, thread_id: str, owner_user_id: str | None, title: str = DEFAULT_TITLE
    ) -> ThreadRecord:
        now = datetime.now(UTC)
        record = ThreadRecord(
            id=thread_id,
            title=title,
            created_at=now,
            updated_at=now,
            owner_user_id=owner_user_id,
        )
        self._rows[thread_id] = record
        self._bump_recency(thread_id)
        return record

    async def create(self, owner_user_id: str, title: str | None) -> ThreadRecord:
        return self.insert(str(uuid.uuid4()), owner_user_id, title if title else DEFAULT_TITLE)

    async def list_for_owner(self, owner_user_id: str) -> list[ThreadRecord]:
        owned = [r for r in self._rows.values() if r.owner_user_id == owner_user_id]
        return sorted(owned, key=lambda r: self._recency.get(r.id, 0), reverse=True)

    async def get(self, thread_id: str, owner_user_id: str) -> ThreadRecord | None:
        record = self._rows.get(thread_id)
        if record is None or record.owner_user_id != owner_user_id:
            return None
        return record

    async def delete(self, thread_id: str, owner_user_id: str) -> bool:
        if await self.get(thread_id, owner_user_id) is None:
            return False
        self._rows.pop(thread_id, None)
        self._recency.pop(thread_id, None)
        return True

    async def adopt_orphans(self, owner_user_id: str) -> int:
        orphans = [r for r in self._rows.values() if r.owner_user_id is None]
        for record in orphans:
            self._rows[record.id] = replace(record, owner_user_id=owner_user_id)
        return len(orphans)

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

    async def set_active_checkpoint_id(
        self, thread_id: str, checkpoint_id: str | None
    ) -> None:
        record = self._rows.get(thread_id)
        if record is None:
            return
        self._rows[thread_id] = replace(record, active_checkpoint_id=checkpoint_id)

"""`RoutineStore`: saved prompts the agent runs later (M17-02).

A routine belongs to one user (`owner_user_id`) and runs as them in one of
their spaces (`space`: `/personal` or `/spaces/<slug>`). `schedule` is the
JSON shape from `app.routines.schedule`; `next_run_at` (UTC) is derived from
it and `timezone`, and is null for a disabled routine or a one-shot that has
run. `grant_token` is the platform routine grant a scheduled run acts with
(M17-03): set while the routine is enabled, never returned by the API. Each
run is a `threads` row with `routine_id` set; deleting the routine
keeps its runs as ordinary chats (`ON DELETE SET NULL`).

Same split as `ThreadStore`: `PgRoutineStore` (row-level security keyed on
`owner_user_id`, `app/db/rls.py`) and `InMemoryRoutineStore` for tests.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, fields, replace
from datetime import UTC, datetime
from typing import Any, Protocol

from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool

# Separate statements: the pool's connections prepare everything they run.
ROUTINES_DDL = (
    """
    CREATE TABLE IF NOT EXISTS routines (
        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        owner_user_id UUID NOT NULL,
        space TEXT NOT NULL,
        name TEXT NOT NULL,
        prompt TEXT NOT NULL,
        schedule JSONB NOT NULL,
        timezone TEXT NOT NULL,
        enabled BOOLEAN NOT NULL DEFAULT true,
        next_run_at TIMESTAMPTZ,
        last_run_at TIMESTAMPTZ,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
    )
    """,
    "CREATE INDEX IF NOT EXISTS routines_owner_idx ON routines (owner_user_id)",
    "CREATE INDEX IF NOT EXISTS routines_due_idx ON routines (next_run_at) WHERE enabled",
    "ALTER TABLE routines ADD COLUMN IF NOT EXISTS grant_token TEXT",
    (
        "ALTER TABLE threads ADD COLUMN IF NOT EXISTS routine_id UUID "
        "REFERENCES routines (id) ON DELETE SET NULL"
    ),
    (
        "CREATE INDEX IF NOT EXISTS threads_routine_idx ON threads (routine_id, created_at DESC) "
        "WHERE routine_id IS NOT NULL"
    ),
)

# What `update` may change.
EDITABLE = frozenset(
    {
        "space",
        "name",
        "prompt",
        "schedule",
        "timezone",
        "enabled",
        "next_run_at",
        "last_run_at",
        "grant_token",
    }
)


@dataclass(frozen=True)
class RoutineRecord:
    id: str
    owner_user_id: str
    space: str
    name: str
    prompt: str
    schedule: dict
    timezone: str
    enabled: bool
    next_run_at: datetime | None
    last_run_at: datetime | None
    created_at: datetime
    updated_at: datetime
    grant_token: str | None = None


@dataclass(frozen=True)
class NewRoutine:
    space: str
    name: str
    prompt: str
    schedule: dict
    timezone: str
    enabled: bool = True
    next_run_at: datetime | None = None


class RoutineStore(Protocol):
    """Every lookup takes the caller's id; another user's routine reads as missing."""

    async def create(self, owner_user_id: str, routine: NewRoutine) -> RoutineRecord: ...

    async def list_for_owner(self, owner_user_id: str) -> list[RoutineRecord]: ...

    async def get(self, routine_id: str, owner_user_id: str) -> RoutineRecord | None: ...

    async def update(
        self, routine_id: str, owner_user_id: str, changes: dict[str, Any]
    ) -> RoutineRecord | None: ...

    async def delete(self, routine_id: str, owner_user_id: str) -> bool: ...


def _is_valid_uuid(value: str) -> bool:
    try:
        uuid.UUID(value)
    except ValueError:
        return False
    return True


def _check_changes(changes: dict[str, Any]) -> None:
    unknown = set(changes) - EDITABLE
    if unknown:
        raise ValueError(f"not editable: {sorted(unknown)}")


_COLUMNS = ", ".join(f.name for f in fields(RoutineRecord))


def _record_from_row(row: dict) -> RoutineRecord:
    return RoutineRecord(
        **{**row, "id": str(row["id"]), "owner_user_id": str(row["owner_user_id"])}
    )


class PgRoutineStore:
    def __init__(self, pool: AsyncConnectionPool) -> None:
        self._pool = pool

    async def create(self, owner_user_id: str, routine: NewRoutine) -> RoutineRecord:
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                "INSERT INTO routines "
                "(owner_user_id, space, name, prompt, schedule, timezone, enabled, next_run_at) "
                f"VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING {_COLUMNS}",
                (
                    owner_user_id,
                    routine.space,
                    routine.name,
                    routine.prompt,
                    Jsonb(routine.schedule),
                    routine.timezone,
                    routine.enabled,
                    routine.next_run_at,
                ),
            )
            row = await cur.fetchone()
        return _record_from_row(row)

    async def list_for_owner(self, owner_user_id: str) -> list[RoutineRecord]:
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                f"SELECT {_COLUMNS} FROM routines WHERE owner_user_id = %s "
                "ORDER BY lower(name), created_at",
                (owner_user_id,),
            )
            rows = await cur.fetchall()
        return [_record_from_row(row) for row in rows]

    async def get(self, routine_id: str, owner_user_id: str) -> RoutineRecord | None:
        if not _is_valid_uuid(routine_id):
            return None
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                f"SELECT {_COLUMNS} FROM routines WHERE id = %s AND owner_user_id = %s",
                (routine_id, owner_user_id),
            )
            row = await cur.fetchone()
        return _record_from_row(row) if row is not None else None

    async def update(
        self, routine_id: str, owner_user_id: str, changes: dict[str, Any]
    ) -> RoutineRecord | None:
        _check_changes(changes)
        if not _is_valid_uuid(routine_id):
            return None
        if not changes:
            return await self.get(routine_id, owner_user_id)
        values = {k: Jsonb(v) if k == "schedule" else v for k, v in changes.items()}
        assignments = ", ".join(f"{column} = %s" for column in values)
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                f"UPDATE routines SET {assignments}, updated_at = now() "
                f"WHERE id = %s AND owner_user_id = %s RETURNING {_COLUMNS}",
                (*values.values(), routine_id, owner_user_id),
            )
            row = await cur.fetchone()
        return _record_from_row(row) if row is not None else None

    async def delete(self, routine_id: str, owner_user_id: str) -> bool:
        if not _is_valid_uuid(routine_id):
            return False
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                "DELETE FROM routines WHERE id = %s AND owner_user_id = %s",
                (routine_id, owner_user_id),
            )
        return cur.rowcount > 0


class InMemoryRoutineStore:
    """Dict-backed `RoutineStore`; `thread_store` gets the `ON DELETE SET NULL`."""

    def __init__(self, thread_store: Any = None) -> None:
        self._rows: dict[str, RoutineRecord] = {}
        self._thread_store = thread_store

    async def create(self, owner_user_id: str, routine: NewRoutine) -> RoutineRecord:
        now = datetime.now(UTC)
        record = RoutineRecord(
            id=str(uuid.uuid4()),
            owner_user_id=owner_user_id,
            space=routine.space,
            name=routine.name,
            prompt=routine.prompt,
            schedule=routine.schedule,
            timezone=routine.timezone,
            enabled=routine.enabled,
            next_run_at=routine.next_run_at,
            last_run_at=None,
            created_at=now,
            updated_at=now,
        )
        self._rows[record.id] = record
        return record

    async def list_for_owner(self, owner_user_id: str) -> list[RoutineRecord]:
        owned = [r for r in self._rows.values() if r.owner_user_id == owner_user_id]
        return sorted(owned, key=lambda r: (r.name.lower(), r.created_at))

    async def get(self, routine_id: str, owner_user_id: str) -> RoutineRecord | None:
        record = self._rows.get(routine_id)
        if record is None or record.owner_user_id != owner_user_id:
            return None
        return record

    async def update(
        self, routine_id: str, owner_user_id: str, changes: dict[str, Any]
    ) -> RoutineRecord | None:
        _check_changes(changes)
        record = await self.get(routine_id, owner_user_id)
        if record is None:
            return None
        if changes:
            record = replace(record, **changes, updated_at=datetime.now(UTC))
            self._rows[routine_id] = record
        return record

    async def delete(self, routine_id: str, owner_user_id: str) -> bool:
        if await self.get(routine_id, owner_user_id) is None:
            return False
        del self._rows[routine_id]
        if self._thread_store is not None:
            self._thread_store.detach_routine(routine_id)
        return True

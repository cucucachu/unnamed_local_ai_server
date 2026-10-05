"""`RoutineStore`: saved prompts the agent runs later (M17-02).

A routine belongs to one user (`owner_user_id`) and runs as them in one of
their spaces (`space`: `/personal` or `/spaces/<slug>`). `schedule` is the
JSON shape from `app.routines.schedule`; `next_run_at` (UTC) is derived from
it and `timezone`, and is null for a disabled routine or a one-shot that has
run. `grant_token` is the platform routine grant a scheduled run acts with
(M17-03): set while the routine is enabled, never returned by the API.

Each run (M17-04) is a `routine_runs` row: how it was started, its status
and timing, and the thread it ran in (`threads.routine_id` set) - none for
a run that never started. Deleting the routine deletes its run records but
keeps their threads as ordinary chats (`ON DELETE SET NULL`). The
scheduler's `claim_due` and `recover_interrupted` work across users, as the
row-level-security bypass role.

Same split as `ThreadStore`: `PgRoutineStore` (row-level security keyed on
`owner_user_id`, `app/db/rls.py`) and `InMemoryRoutineStore` for tests.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass, fields, replace
from datetime import UTC, datetime, timedelta
from typing import Any, Literal, Protocol

from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool

from app.db.rls import system_transaction

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
    """
    CREATE TABLE IF NOT EXISTS routine_runs (
        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        routine_id UUID NOT NULL REFERENCES routines (id) ON DELETE CASCADE,
        owner_user_id UUID NOT NULL,
        trigger TEXT NOT NULL CHECK (trigger IN ('schedule', 'manual')),
        status TEXT NOT NULL CHECK (status IN ('queued', 'running', 'succeeded', 'failed',
                                               'timed_out', 'missed', 'waiting_approval')),
        detail TEXT,
        thread_id UUID REFERENCES threads (id) ON DELETE SET NULL,
        due_at TIMESTAMPTZ,
        started_at TIMESTAMPTZ,
        finished_at TIMESTAMPTZ,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now()
    )
    """,
    (
        "CREATE INDEX IF NOT EXISTS routine_runs_routine_idx "
        "ON routine_runs (routine_id, created_at DESC)"
    ),
    (
        "CREATE INDEX IF NOT EXISTS routine_runs_active_idx ON routine_runs (status) "
        "WHERE status IN ('queued', 'running')"
    ),
)

RunStatus = Literal[
    "queued", "running", "succeeded", "failed", "timed_out", "missed", "waiting_approval"
]
Trigger = Literal["schedule", "manual"]
INTERRUPTED = "interrupted by a server restart"

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


@dataclass(frozen=True)
class RunRecord:
    id: str
    routine_id: str
    owner_user_id: str
    trigger: Trigger
    status: RunStatus
    detail: str | None
    thread_id: str | None
    due_at: datetime | None
    started_at: datetime | None
    finished_at: datetime | None
    created_at: datetime


# What `update_run` may change.
RUN_EDITABLE = frozenset({"status", "detail", "thread_id", "started_at", "finished_at"})

# A claimed routine's next run after `now`; None retires it (a one-shot).
Advance = Callable[[RoutineRecord, datetime], datetime | None]


def _late(due_at: datetime, now: datetime, grace: timedelta) -> tuple[RunStatus, str | None]:
    if now - due_at > grace:
        return "missed", f"not started within {_minutes(grace)} of its time"
    return "queued", None


def _minutes(span: timedelta) -> str:
    minutes = round(span.total_seconds() / 60)
    return f"{minutes // 60} h" if minutes % 60 == 0 else f"{minutes} min"


class RoutineStore(Protocol):
    """Every lookup takes the caller's id; another user's routine reads as missing."""

    async def create(self, owner_user_id: str, routine: NewRoutine) -> RoutineRecord: ...

    async def list_for_owner(self, owner_user_id: str) -> list[RoutineRecord]: ...

    async def get(self, routine_id: str, owner_user_id: str) -> RoutineRecord | None: ...

    async def update(
        self, routine_id: str, owner_user_id: str, changes: dict[str, Any]
    ) -> RoutineRecord | None: ...

    async def delete(self, routine_id: str, owner_user_id: str) -> bool: ...

    async def create_run(
        self,
        routine: RoutineRecord,
        trigger: Trigger,
        status: RunStatus,
        *,
        thread_id: str | None = None,
        started_at: datetime | None = None,
    ) -> RunRecord: ...

    async def update_run(
        self, run_id: str, owner_user_id: str, changes: dict[str, Any]
    ) -> RunRecord | None: ...

    async def list_runs(self, routine_id: str, owner_user_id: str) -> list[RunRecord]:
        """Newest first."""
        ...

    async def claim_due(
        self, now: datetime, limit: int, advance: Advance, grace: timedelta
    ) -> list[tuple[RoutineRecord, RunRecord]]:
        """Every user's routines due by `now`, each moved on to its next run in
        the same transaction (so a routine is claimed once per due time, even
        across a restart or a second process), with a `queued` run record -
        or `missed`, if it's more than `grace` late."""
        ...

    async def recover_interrupted(self) -> list[tuple[RoutineRecord, RunRecord]]:
        """At startup: fail the runs a restart cut off; the queued ones are
        returned, with their routines, to queue again."""
        ...


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
_RUN_COLUMNS = ", ".join(f.name for f in fields(RunRecord))


def _record_from_row(row: dict) -> RoutineRecord:
    return RoutineRecord(
        **{**row, "id": str(row["id"]), "owner_user_id": str(row["owner_user_id"])}
    )


def _run_from_row(row: dict) -> RunRecord:
    ids = ("id", "routine_id", "owner_user_id", "thread_id")
    return RunRecord(**{**row, **{k: str(row[k]) if row[k] else None for k in ids}})


def _check_run_changes(changes: dict[str, Any]) -> None:
    unknown = set(changes) - RUN_EDITABLE
    if unknown:
        raise ValueError(f"not editable: {sorted(unknown)}")


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

    async def create_run(
        self,
        routine: RoutineRecord,
        trigger: Trigger,
        status: RunStatus,
        *,
        thread_id: str | None = None,
        started_at: datetime | None = None,
    ) -> RunRecord:
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                "INSERT INTO routine_runs "
                "(routine_id, owner_user_id, trigger, status, thread_id, started_at) "
                f"VALUES (%s, %s, %s, %s, %s, %s) RETURNING {_RUN_COLUMNS}",
                (routine.id, routine.owner_user_id, trigger, status, thread_id, started_at),
            )
            row = await cur.fetchone()
        return _run_from_row(row)

    async def update_run(
        self, run_id: str, owner_user_id: str, changes: dict[str, Any]
    ) -> RunRecord | None:
        _check_run_changes(changes)
        if not changes or not _is_valid_uuid(run_id):
            return None
        assignments = ", ".join(f"{column} = %s" for column in changes)
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                f"UPDATE routine_runs SET {assignments} "
                f"WHERE id = %s AND owner_user_id = %s RETURNING {_RUN_COLUMNS}",
                (*changes.values(), run_id, owner_user_id),
            )
            row = await cur.fetchone()
        return _run_from_row(row) if row is not None else None

    async def list_runs(self, routine_id: str, owner_user_id: str) -> list[RunRecord]:
        if not _is_valid_uuid(routine_id):
            return []
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                f"SELECT {_RUN_COLUMNS} FROM routine_runs "
                "WHERE routine_id = %s AND owner_user_id = %s ORDER BY created_at DESC",
                (routine_id, owner_user_id),
            )
            rows = await cur.fetchall()
        return [_run_from_row(row) for row in rows]

    async def claim_due(
        self, now: datetime, limit: int, advance: Advance, grace: timedelta
    ) -> list[tuple[RoutineRecord, RunRecord]]:
        claimed = []
        async with system_transaction(self._pool) as conn:
            cur = await conn.execute(
                f"SELECT {_COLUMNS} FROM routines WHERE enabled AND next_run_at <= %s "
                "ORDER BY next_run_at LIMIT %s FOR UPDATE SKIP LOCKED",
                (now, limit),
            )
            for row in await cur.fetchall():
                routine = _record_from_row(row)
                next_at = advance(routine, now)
                cur = await conn.execute(
                    "UPDATE routines SET next_run_at = %s, enabled = %s "
                    f"WHERE id = %s RETURNING {_COLUMNS}",
                    (next_at, next_at is not None, routine.id),
                )
                advanced = _record_from_row(await cur.fetchone())
                status, detail = _late(routine.next_run_at, now, grace)
                cur = await conn.execute(
                    "INSERT INTO routine_runs "
                    "(routine_id, owner_user_id, trigger, status, detail, due_at, finished_at) "
                    f"VALUES (%s, %s, 'schedule', %s, %s, %s, %s) RETURNING {_RUN_COLUMNS}",
                    (
                        routine.id,
                        routine.owner_user_id,
                        status,
                        detail,
                        routine.next_run_at,
                        now if status == "missed" else None,
                    ),
                )
                claimed.append((advanced, _run_from_row(await cur.fetchone())))
        return claimed

    async def recover_interrupted(self) -> list[tuple[RoutineRecord, RunRecord]]:
        async with system_transaction(self._pool) as conn:
            await conn.execute(
                "UPDATE routine_runs SET status = 'failed', detail = %s, finished_at = now() "
                "WHERE status = 'running'",
                (INTERRUPTED,),
            )
            cur = await conn.execute(
                f"SELECT {_RUN_COLUMNS} FROM routine_runs WHERE status = 'queued' "
                "ORDER BY due_at"
            )
            runs = [_run_from_row(row) for row in await cur.fetchall()]
            cur = await conn.execute(
                f"SELECT {_COLUMNS} FROM routines WHERE id = ANY(%s::uuid[])",
                ([run.routine_id for run in runs],),
            )
            routines = {r.id: r for r in map(_record_from_row, await cur.fetchall())}
        return [(routines[run.routine_id], run) for run in runs]


class InMemoryRoutineStore:
    """Dict-backed `RoutineStore`; `thread_store` gets the `ON DELETE SET NULL`."""

    def __init__(self, thread_store: Any = None) -> None:
        self._rows: dict[str, RoutineRecord] = {}
        self._runs: dict[str, RunRecord] = {}
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
        self._runs = {k: r for k, r in self._runs.items() if r.routine_id != routine_id}
        if self._thread_store is not None:
            self._thread_store.detach_routine(routine_id)
        return True

    def _add_run(self, routine: RoutineRecord, **values: Any) -> RunRecord:
        run = RunRecord(
            **{
                "id": str(uuid.uuid4()),
                "routine_id": routine.id,
                "owner_user_id": routine.owner_user_id,
                "detail": None,
                "thread_id": None,
                "due_at": None,
                "started_at": None,
                "finished_at": None,
                "created_at": datetime.now(UTC),
                **values,
            }
        )
        self._runs[run.id] = run
        return run

    async def create_run(
        self,
        routine: RoutineRecord,
        trigger: Trigger,
        status: RunStatus,
        *,
        thread_id: str | None = None,
        started_at: datetime | None = None,
    ) -> RunRecord:
        return self._add_run(
            routine, trigger=trigger, status=status, thread_id=thread_id, started_at=started_at
        )

    async def update_run(
        self, run_id: str, owner_user_id: str, changes: dict[str, Any]
    ) -> RunRecord | None:
        _check_run_changes(changes)
        run = self._runs.get(run_id)
        if run is None or run.owner_user_id != owner_user_id or not changes:
            return None
        run = replace(run, **changes)
        self._runs[run_id] = run
        return run

    async def list_runs(self, routine_id: str, owner_user_id: str) -> list[RunRecord]:
        runs = [
            r
            for r in self._runs.values()
            if r.routine_id == routine_id and r.owner_user_id == owner_user_id
        ]
        return sorted(runs, key=lambda r: r.created_at, reverse=True)

    async def claim_due(
        self, now: datetime, limit: int, advance: Advance, grace: timedelta
    ) -> list[tuple[RoutineRecord, RunRecord]]:
        due = sorted(
            (r for r in self._rows.values() if r.enabled and r.next_run_at and r.next_run_at <= now),
            key=lambda r: r.next_run_at,
        )[:limit]
        claimed = []
        for routine in due:
            next_at = advance(routine, now)
            advanced = replace(routine, next_run_at=next_at, enabled=next_at is not None)
            self._rows[routine.id] = advanced
            status, detail = _late(routine.next_run_at, now, grace)
            run = self._add_run(
                routine,
                trigger="schedule",
                status=status,
                detail=detail,
                due_at=routine.next_run_at,
                finished_at=now if status == "missed" else None,
            )
            claimed.append((advanced, run))
        return claimed

    async def recover_interrupted(self) -> list[tuple[RoutineRecord, RunRecord]]:
        now = datetime.now(UTC)
        for run in list(self._runs.values()):
            if run.status == "running":
                self._runs[run.id] = replace(
                    run, status="failed", detail=INTERRUPTED, finished_at=now
                )
        queued = sorted(
            (r for r in self._runs.values() if r.status == "queued"),
            key=lambda r: r.due_at or r.created_at,
        )
        return [(self._rows[run.routine_id], run) for run in queued]

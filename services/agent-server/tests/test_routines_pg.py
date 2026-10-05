"""`PgRoutineStore` and `threads.routine_id` (M17-02) against a real Postgres.

Same throwaway server as `test_threads_pg.py`, connected to as `agent`, so
row-level security applies. Skipped without the `docker` CLI.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest

from app.db import rls
from app.db.checkpointer import build_postgres_checkpointer
from app.db.routines import NewRoutine, PgRoutineStore
from app.db.threads import PgThreadStore
from tests.conftest import PgServer

pytestmark = pytest.mark.integration

SCHEDULE = {"kind": "daily", "time": "08:30:00"}


def _new(name: str = "Brief", **overrides) -> NewRoutine:
    fields = {
        "space": "/personal",
        "name": name,
        "prompt": "Do the thing.",
        "schedule": SCHEDULE,
        "timezone": "Europe/Berlin",
        "next_run_at": datetime(2026, 10, 6, 6, 30, tzinfo=UTC),
        **overrides,
    }
    return NewRoutine(**fields)


async def test_round_trip_and_owner_isolation(pg_server: PgServer) -> None:
    pg = await build_postgres_checkpointer(pg_server.agent_dsn)
    store = PgRoutineStore(pg.pool)
    owner, other = str(uuid.uuid4()), str(uuid.uuid4())
    try:
        rls.bind_user(owner)
        created = await store.create(owner, _new("b"))
        assert created.schedule == SCHEDULE
        assert created.enabled is True and created.last_run_at is None
        assert await store.get(created.id, owner) == created
        second = await store.create(owner, _new("A"))
        assert [r.id for r in await store.list_for_owner(owner)] == [second.id, created.id]

        ran = datetime(2026, 10, 6, 6, 30, 5, tzinfo=UTC)
        updated = await store.update(
            created.id,
            owner,
            {"schedule": {"kind": "monthly", "day": 1, "time": "09:00:00"}, "last_run_at": ran},
        )
        assert updated.schedule["kind"] == "monthly"
        assert updated.last_run_at == ran
        assert updated.updated_at > created.updated_at
        assert created.grant_token is None
        granted = await store.update(created.id, owner, {"grant_token": "hr_secret"})
        assert (await store.get(created.id, owner)).grant_token == granted.grant_token == "hr_secret"
        with pytest.raises(ValueError):
            await store.update(created.id, owner, {"owner_user_id": other})
        assert await store.get("not-a-uuid", owner) is None

        # Another user sees nothing, even asking with the owner's id.
        rls.bind_user(other)
        assert await store.list_for_owner(owner) == []
        assert await store.get(created.id, owner) is None
        assert await store.update(created.id, owner, {"name": "x"}) is None
        assert await store.delete(created.id, owner) is False
        async with pg.pool.connection() as conn:
            rows = await (await conn.execute("SELECT count(*) AS n FROM routines")).fetchone()
        assert rows["n"] == 0

        rls.bind_user(owner)
        assert await store.delete(created.id, owner) is True
        assert await store.get(created.id, owner) is None
    finally:
        rls.bind_user(owner)
        async with pg.pool.connection() as conn:
            await conn.execute("DELETE FROM routines WHERE owner_user_id = %s", (owner,))
        await pg.close()


async def test_runs_are_threads_that_outlive_their_routine(pg_server: PgServer) -> None:
    pg = await build_postgres_checkpointer(pg_server.agent_dsn)
    routines, threads = PgRoutineStore(pg.pool), PgThreadStore(pg.pool)
    owner = str(uuid.uuid4())
    rls.bind_user(owner)
    try:
        routine = await routines.create(owner, _new())
        chat = await threads.create(owner, "just a chat")
        first = await threads.create(owner, "Brief · Oct 6", routine.id)
        second = await threads.create(owner, "Brief · Oct 7", routine.id)
        await threads.touch(first.id)

        runs = await threads.list_for_owner(owner, routine_id=routine.id)
        assert [r.id for r in runs] == [second.id, first.id]
        assert all(r.routine_id == routine.id for r in runs)
        assert await threads.list_for_owner(owner, routine_id="nope") == []
        assert {r.id for r in await threads.list_for_owner(owner)} == {chat.id, first.id, second.id}

        assert await routines.delete(routine.id, owner)
        kept = await threads.get(first.id, owner)
        assert kept is not None and kept.routine_id is None
    finally:
        async with pg.pool.connection() as conn:
            await conn.execute("DELETE FROM threads WHERE owner_user_id = %s", (owner,))
            await conn.execute("DELETE FROM routines WHERE owner_user_id = %s", (owner,))
        await pg.close()

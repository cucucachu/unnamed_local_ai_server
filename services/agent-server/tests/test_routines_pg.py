"""`PgRoutineStore`, `routine_runs` and `threads.routine_id` (M17-02, -04) on a real Postgres.

Same throwaway server as `test_threads_pg.py`, connected to as `agent`, so
row-level security applies. Skipped without the `docker` CLI.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app.db import rls
from app.db.checkpointer import build_postgres_checkpointer
from app.db.routines import INTERRUPTED, NewRoutine, PgRoutineStore
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


def _advance(routine, now):
    return None if routine.name == "once" else now + timedelta(days=1)


async def test_claims_are_cross_user_exclusive_and_advance(pg_server: PgServer) -> None:
    pg = await build_postgres_checkpointer(pg_server.agent_dsn)
    store = PgRoutineStore(pg.pool)
    alice, bob = str(uuid.uuid4()), str(uuid.uuid4())
    now = datetime(2026, 10, 6, 7, 0, tzinfo=UTC)
    try:
        rls.bind_user(alice)
        daily = await store.create(alice, _new("daily", next_run_at=now - timedelta(minutes=5)))
        await store.create(alice, _new("later", next_run_at=now + timedelta(hours=1)))
        rls.bind_user(bob)
        once = await store.create(bob, _new("once", next_run_at=now - timedelta(hours=3)))

        # Two schedulers claiming at once: each due routine goes to exactly one.
        rls.bind_user(None)
        first, second = await asyncio.gather(
            store.claim_due(now, 10, _advance, timedelta(hours=1)),
            store.claim_due(now, 10, _advance, timedelta(hours=1)),
        )
        claimed = [c for c in first + second if c[0].owner_user_id in (alice, bob)]
        by_name = {routine.name: (routine, run) for routine, run in claimed}
        assert sorted(by_name) == ["daily", "once"] and len(claimed) == 2
        routine, run = by_name["daily"]
        assert routine.next_run_at == now + timedelta(days=1) and routine.enabled
        assert (run.status, run.due_at, run.trigger) == ("queued", daily.next_run_at, "schedule")
        routine, run = by_name["once"]
        assert (routine.enabled, routine.next_run_at) == (False, None)
        assert (run.status, run.finished_at) == ("missed", now)
        again = await store.claim_due(now, 10, _advance, timedelta(hours=1))
        assert all(r.owner_user_id not in (alice, bob) for r, _ in again)

        # Run records are the owner's alone.
        rls.bind_user(alice)
        (listed,) = await store.list_runs(daily.id, alice)
        running = await store.update_run(listed.id, alice, {"status": "running"})
        assert running.status == "running"
        assert await store.list_runs(once.id, bob) == []
        rls.bind_user(bob)
        assert await store.update_run(listed.id, alice, {"status": "failed"}) is None
        assert [r.status for r in await store.list_runs(once.id, bob)] == ["missed"]

        # A restart fails what was running; nothing of these is left queued.
        rls.bind_user(None)
        requeue = await store.recover_interrupted()
        assert all(r.owner_user_id not in (alice, bob) for r, _ in requeue)
        rls.bind_user(alice)
        (failed,) = await store.list_runs(daily.id, alice)
        assert (failed.status, failed.detail) == ("failed", INTERRUPTED)
        assert failed.finished_at is not None

        # Deleting the routine deletes its run records.
        manual = await store.create_run(daily, "manual", "queued")
        rls.bind_user(None)
        assert [r.id for _, r in await store.recover_interrupted()
                if r.owner_user_id == alice] == [manual.id]  # fmt: skip
        rls.bind_user(alice)
        assert await store.delete(daily.id, alice)
        assert await store.list_runs(daily.id, alice) == []
    finally:
        for owner in (alice, bob):
            rls.bind_user(owner)
            async with pg.pool.connection() as conn:
                await conn.execute("DELETE FROM routines WHERE owner_user_id = %s", (owner,))
        await pg.close()


async def test_paused_runs(pg_server: PgServer) -> None:
    pg = await build_postgres_checkpointer(pg_server.agent_dsn)
    routines, threads = PgRoutineStore(pg.pool), PgThreadStore(pg.pool)
    alice, bob = str(uuid.uuid4()), str(uuid.uuid4())
    paused_at = datetime(2026, 10, 6, 7, 0, tzinfo=UTC)
    try:
        rls.bind_user(alice)
        routine = await routines.create(alice, _new(approval_mode="allow_writes"))
        assert routine.approval_mode == "allow_writes"
        thread = await threads.create(alice, "Brief · Oct 6", routine.id)
        run = await routines.create_run(routine, "schedule", "running", thread_id=thread.id)
        done = await routines.create_run(routine, "manual", "running")
        await routines.update_run(done.id, alice, {"status": "succeeded", "finished_at": paused_at})
        paused = await routines.update_run(
            run.id, alice, {"status": "waiting_approval", "finished_at": paused_at}
        )
        assert await routines.run_for_thread(thread.id, alice) == paused
        assert await routines.run_for_thread("not-a-uuid", alice) is None
        assert await routines.waiting_thread_ids(alice) == {thread.id}
        latest = await routines.latest_runs(alice)
        assert {k: v.id for k, v in latest.items()} == {routine.id: done.id}

        rls.bind_user(None)
        stale = await routines.stale_waiting(paused_at + timedelta(seconds=1))
        assert [r.id for _, r in stale if r.owner_user_id == alice] == [run.id]
        assert all(r.owner_user_id != alice for _, r in await routines.stale_waiting(paused_at))

        rls.bind_user(bob)
        assert await routines.waiting_thread_ids(bob) == set()
    finally:
        rls.bind_user(alice)
        async with pg.pool.connection() as conn:
            await conn.execute("DELETE FROM threads WHERE owner_user_id = %s", (alice,))
            await conn.execute("DELETE FROM routines WHERE owner_user_id = %s", (alice,))
        await pg.close()

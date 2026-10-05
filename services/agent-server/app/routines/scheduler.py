"""The routine scheduler (M17-04): due routines run headlessly, one GPU's worth at a time.

Every `poll_s` it claims every user's due routines (`RoutineStore.claim_due`:
`FOR UPDATE SKIP LOCKED`, `next_run_at` moved past now in the same
transaction, so nothing fires twice and a backlog never piles up: a routine
whose server was down for three of its times runs once). Each claim gets a
`routine_runs` record:

- `missed` straight away when more than `grace` late (the server was down);
- else `queued`, for a worker. Workers start a run only while fewer than
  `max_concurrent` turns of any kind are running - chats never wait on
  routines, routines wait on chats. A run still waiting past `grace` (a
  queue backlog) is `missed` too.

A started run gets its thread and a delegation from the routine's grant
(M17-03; a refused grant disables the routine and fails the run), then
runs on the detached turn runner until it ends or `run_timeout_s` cancels
it (`app.routines.runs.finish_run` records the outcome). A one-shot is
disabled when claimed, and its grant revoked once its run is over.

At startup, runs a restart cut off are failed and queued ones are queued
again. Run-now (`start_manual`) skips the queue - someone asked for it -
but is recorded and time-limited the same way.

A run paused on an approval (M17-05) stays `waiting_approval` until it's
answered in its chat (`track_resumed` records how it ends then) or
`approval_ttl` passes: each poll then answers it reject-all, under the
same model cap and the routine's grant, and records it `expired`.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from app.agent.turn_runner import ActiveTurn, TurnRequest
from app.api import chat_ws as ws
from app.core.delegation import Delegation, DelegationDenied, DelegationUnavailable
from app.db.rls import bind_user
from app.db.routines import RoutineRecord, RunRecord
from app.db.threads import ThreadRecord
from app.routines import runs
from app.routines.schedule import next_run, parse_schedule

logger = logging.getLogger(__name__)

CLAIM_BATCH = 20
EXPIRED_MESSAGE = "Nobody answered this approval in time, so it was rejected."


def advance(routine: RoutineRecord, now: datetime) -> datetime | None:
    """The routine's next run after `now`; None for a one-shot (it's retired)."""
    schedule = parse_schedule(routine.schedule)
    if schedule.kind == "once":
        return None
    return next_run(schedule, routine.timezone, now)


def _is_once(routine: RoutineRecord) -> bool:
    return routine.schedule.get("kind") == "once"


class RoutineScheduler:
    def __init__(
        self,
        app_state: Any,
        *,
        poll_s: float,
        max_concurrent: int,
        grace: timedelta,
        run_timeout_s: float,
        approval_ttl: timedelta = timedelta(days=1),
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._state = app_state
        self._approval_ttl = approval_ttl
        self._poll_s = poll_s
        self._max_concurrent = max_concurrent
        self._grace = grace
        self._run_timeout_s = run_timeout_s
        self._clock = clock
        self._queue: asyncio.Queue[tuple[RoutineRecord, RunRecord]] = asyncio.Queue()
        self._start_lock = asyncio.Lock()
        self._tasks: set[asyncio.Task] = set()

    # --- lifecycle -----------------------------------------------------------------

    async def start(self, *, poll: bool = True) -> None:
        """Recover from the last shutdown, start the workers, and (with `poll`) the clock."""
        for claim in await self._state.routine_store.recover_interrupted():
            self._queue.put_nowait(claim)
        for _ in range(self._max_concurrent):
            self._spawn(self._worker())
        if poll:
            self._spawn(self._poll())

    async def stop(self) -> None:
        tasks = list(self._tasks)
        for task in tasks:
            task.cancel()
        for task in tasks:
            with contextlib.suppress(BaseException):
                await task

    async def idle(self) -> None:
        """Until every queued run has been started and finished (tests)."""
        await self._queue.join()

    def _spawn(self, coro) -> asyncio.Task:
        task = asyncio.create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    # --- claiming ------------------------------------------------------------------

    async def tick(self) -> list[RunRecord]:
        """Claim what's due now; queue it (or retire a missed one-shot)."""
        claimed = await self._state.routine_store.claim_due(
            self._clock(), CLAIM_BATCH, advance, self._grace
        )
        for routine, run in claimed:
            if run.status == "queued":
                self._queue.put_nowait((routine, run))
            else:
                logger.info("routine %s: %s (%s)", routine.id, run.status, run.detail)
                await self._retire_if_once(routine)
        return [run for _, run in claimed]

    async def _poll(self) -> None:
        while True:
            try:
                await self.tick()
            except Exception:
                logger.exception("routine scheduler: claiming due routines failed")
            try:
                await self.expire_stale()
            except Exception:
                logger.exception("routine scheduler: expiring stale approvals failed")
            await asyncio.sleep(self._poll_s)

    # --- running -------------------------------------------------------------------

    async def _worker(self) -> None:
        while True:
            routine, run = await self._queue.get()
            try:
                await self._execute(routine, run)
            except Exception as exc:
                logger.exception("routine %s: run %s failed to start", routine.id, run.id)
                await self._finish(run, "failed", str(exc)[: runs.MAX_DETAIL] or "error")
            finally:
                self._queue.task_done()

    async def _execute(self, claimed: RoutineRecord, run: RunRecord) -> None:
        bind_user(claimed.owner_user_id)
        state = self._state
        async with self._start_lock:
            await state.turn_runner.wait_below(self._max_concurrent)
            routine = await state.routine_store.get(claimed.id, claimed.owner_user_id)
            if routine is None:
                return  # deleted while queued; its run record went with it
            if run.due_at is not None and self._clock() - run.due_at > self._grace:
                await self._finish(run, "missed", "waited too long for a free model slot")
                await self._retire_if_once(routine)
                return
            if not routine.enabled and not _is_once(routine):
                await self._finish(run, "missed", "the routine was turned off")
                return
            thread = await runs.create_run_thread(state, routine)
            delegation = await self._delegation(routine, run, thread)
            if delegation is None:
                await self._retire_if_once(routine)
                return
            run = await state.routine_store.update_run(
                run.id,
                run.owner_user_id,
                {"status": "running", "thread_id": thread.id, "started_at": self._clock()},
            )
            turn = await runs.launch_run(state, routine, thread, delegation)
        await self._finish_turn(run, turn)

    async def _delegation(
        self, routine: RoutineRecord, run: RunRecord, thread: ThreadRecord
    ) -> Delegation | None:
        try:
            delegation = await runs.grant_delegation(self._state, routine, thread.id)
        except DelegationUnavailable:
            delegation, detail = None, "couldn't reach the platform"
        else:
            detail = "no longer allowed to run (its permission was revoked); turned off"
        if delegation is None:
            await self._state.thread_store.delete(thread.id, routine.owner_user_id)
            await self._finish(run, "failed", detail)
        return delegation

    async def start_manual(
        self, routine: RoutineRecord, thread: ThreadRecord, delegation: Delegation
    ) -> RunRecord:
        """Run now, on the caller's delegation: no queue, same record and time limit."""
        state = self._state
        run = await state.routine_store.create_run(
            routine, "manual", "running", thread_id=thread.id, started_at=self._clock()
        )
        turn = await runs.launch_run(state, routine, thread, delegation)
        self._spawn(runs.finish_run(state, run, turn, self._run_timeout_s, self._clock))
        return run

    # --- paused runs (M17-05) --------------------------------------------------------

    def track_resumed(self, run: RunRecord, turn: ActiveTurn) -> None:
        """A paused run's approval was answered in its chat: record how it ends now."""
        self._spawn(self._follow_resumed(run, turn))

    async def _follow_resumed(self, run: RunRecord, turn: ActiveTurn) -> None:
        bind_user(run.owner_user_id)
        run = await self._running_again(run)
        await self._finish_turn(run, turn)

    async def _running_again(self, run: RunRecord) -> RunRecord:
        updated = await self._state.routine_store.update_run(
            run.id, run.owner_user_id, {"status": "running", "detail": None, "finished_at": None}
        )
        return updated or run

    async def _finish_turn(
        self, run: RunRecord, turn: ActiveTurn, *, expiring: bool = False
    ) -> None:
        run = await runs.finish_run(self._state, run, turn, self._run_timeout_s, self._clock)
        if expiring and run.status == "succeeded":
            await self._finish(run, "expired", "nobody answered its approval in time; rejected")
        if run.status != "waiting_approval":
            routine = await self._state.routine_store.get(run.routine_id, run.owner_user_id)
            if routine is not None:
                await self._retire_if_once(routine)

    async def expire_stale(self) -> None:
        """Answer reject-all every approval left waiting longer than `approval_ttl`."""
        stale = await self._state.routine_store.stale_waiting(self._clock() - self._approval_ttl)
        for routine, run in stale:
            try:
                await self._expire(routine, run)
            except Exception:
                logger.exception("routine %s: expiring run %s failed", routine.id, run.id)

    async def _expire(self, routine: RoutineRecord, run: RunRecord) -> None:
        bind_user(routine.owner_user_id)
        runner = self._state.turn_runner
        if run.thread_id is None:
            await self._finish(run, "expired", "nobody answered its approval in time")
            return
        if runner.active(run.thread_id) is not None:
            return  # being answered right now
        async with self._start_lock:
            await runner.wait_below(self._max_concurrent)
            lock = runner.lock_for(run.thread_id)
            await lock.acquire()
            started = None
            try:
                started = await self._resume_rejected(routine, run)
            finally:
                if started is None:
                    lock.release()
        if started is None:
            return
        turn, queue = started
        turn.detach(queue)
        run = await self._running_again(run)
        self._spawn(self._finish_turn(run, turn, expiring=True))

    async def _resume_rejected(
        self, routine: RoutineRecord, run: RunRecord
    ) -> tuple[ActiveTurn, asyncio.Queue] | None:
        """Under the thread's lock: resume its pending approval reject-all.

        None when there's nothing to resume (the run is recorded as such)
        or the platform can't be reached (tried again next poll).
        """
        state, owner, thread_id = self._state, routine.owner_user_id, run.thread_id
        tip = await ws.active_checkpoint_id_for(state.thread_store, thread_id, owner)
        paused = await state.agent.aget_state(ws.graph_config(thread_id, tip))
        pending = ws._pending_approval_from_state(paused)
        if pending is None:
            await self._finish(run, "succeeded", "its approval was answered in its chat")
            return None
        try:
            delegation = await runs.grant_delegation(state, routine, thread_id)
        except DelegationUnavailable:
            return None
        if delegation is None:
            await self._finish(
                run, "expired", "nobody answered its approval in time; it can't run any more"
            )
            return None
        document = await state.settings_store.get_document(owner)
        decisions = ws._reject_all_decisions(pending, EXPIRED_MESSAGE)
        return await state.turn_runner.start(
            TurnRequest(
                thread_id=thread_id,
                user_id=owner,
                run_input=ws._resume_command(pending, decisions),
                hitl_enabled=document.hitl_enabled,
                thinking_enabled=document.thinking_enabled,
                checkpoint_id=tip,
                delegation=delegation,
                approval_mode=(paused.metadata or {}).get("approval_mode"),
            ),
            lock_held=True,
        )

    async def _finish(self, run: RunRecord, status: str, detail: str) -> None:
        await self._state.routine_store.update_run(
            run.id,
            run.owner_user_id,
            {"status": status, "detail": detail, "finished_at": self._clock()},
        )

    async def _retire_if_once(self, routine: RoutineRecord) -> None:
        """A one-shot is done after its one run (or miss): drop its grant."""
        if not _is_once(routine):
            return
        bind_user(routine.owner_user_id)
        current = await self._state.routine_store.get(routine.id, routine.owner_user_id)
        if current is None or current.enabled or not current.grant_token:
            return
        try:
            await self._state.delegation_client.revoke_routine_grant(current.grant_token)
        except (DelegationDenied, DelegationUnavailable):
            logger.warning("routine %s: couldn't revoke its grant; it stays listed", routine.id)
            return
        await self._state.routine_store.update(
            routine.id, routine.owner_user_id, {"grant_token": None}
        )

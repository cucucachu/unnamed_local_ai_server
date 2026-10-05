"""A routine's run: start it, and record how it ended (M17-02, M17-04).

A run is a fresh thread (`routine_id` set) whose first user message is the
routine's prompt, run headlessly by the detached turn runner (M17-01) as
the routine's owner: on the caller's delegation for run-now, on one
exchanged from the routine's grant (`grant_delegation`) when scheduled.
Its `routine_runs` record follows it from `running` to `succeeded`,
`failed`, `timed_out` (cancelled after the run timeout) or
`waiting_approval` (a HITL approval is pending in its thread).
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from langchain_core.messages import HumanMessage

from app.agent.turn_runner import ActiveTurn, TurnOutcome, TurnRequest
from app.core.delegation import Delegation, DelegationDenied
from app.db.routines import RoutineRecord, RunRecord, RunStatus
from app.db.threads import ThreadRecord
from app.routines.schedule import zone

logger = logging.getLogger(__name__)


def run_title(routine: RoutineRecord, started: datetime) -> str:
    local = started.astimezone(zone(routine.timezone))
    return f"{routine.name} · {local:%b} {local.day}"


def run_message(routine: RoutineRecord) -> str:
    return f'Routine "{routine.name}" (space {routine.space}):\n\n{routine.prompt}'


async def create_run_thread(app_state: Any, routine: RoutineRecord) -> ThreadRecord:
    return await app_state.thread_store.create(
        routine.owner_user_id, run_title(routine, datetime.now(UTC)), routine.id
    )


async def grant_delegation(
    app_state: Any, routine: RoutineRecord, thread_id: str
) -> Delegation | None:
    """An unattended run's delegation from the routine's grant (M17-03).

    None, with the routine disabled, once the grant is gone or dead (revoked
    in Settings -> Sessions, password changed, edit rights lost, ...).
    `DelegationUnavailable` propagates: the platform couldn't be asked.
    """
    client = app_state.delegation_client
    if routine.grant_token is not None:
        try:
            grant = await client.exchange_routine_grant(routine.grant_token, routine.id, thread_id)
        except DelegationDenied:
            pass
        else:
            return Delegation(client, grant)
    logger.info("routine %s: its grant is no longer valid; disabling it", routine.id)
    await app_state.routine_store.update(
        routine.id,
        routine.owner_user_id,
        {"enabled": False, "next_run_at": None, "grant_token": None},
    )
    return None


async def launch_run(
    app_state: Any, routine: RoutineRecord, thread: ThreadRecord, delegation: Delegation
) -> ActiveTurn:
    """Start the run's turn in the background; nobody is attached to it."""
    document = await app_state.settings_store.get_document(routine.owner_user_id)
    turn, queue = await app_state.turn_runner.start(
        TurnRequest(
            thread_id=thread.id,
            user_id=routine.owner_user_id,
            run_input={"messages": [HumanMessage(content=run_message(routine))]},
            hitl_enabled=document.hitl_enabled,
            thinking_enabled=document.thinking_enabled,
            delegation=delegation,
            approval_mode=routine.approval_mode,
        )
    )
    turn.detach(queue)
    await app_state.routine_store.update(
        routine.id, routine.owner_user_id, {"last_run_at": datetime.now(UTC)}
    )
    return turn


MAX_DETAIL = 500


def _ended(outcome: TurnOutcome, timed_out: bool) -> tuple[RunStatus, str | None]:
    if timed_out:
        return "timed_out", "cancelled: still running at the run time limit"
    if outcome.status == "completed":
        return "succeeded", None
    if outcome.status == "awaiting_approval":
        return "waiting_approval", "waiting for an approval in its chat"
    if outcome.status == "cancelled":
        return "failed", "cancelled"
    if isinstance(outcome.error, DelegationDenied):
        return "failed", "lost access: the routine's permission was revoked"
    error = outcome.error
    return "failed", (str(error) or type(error).__name__)[:MAX_DETAIL] if error else None


async def finish_run(
    app_state: Any,
    run: RunRecord,
    turn: ActiveTurn,
    timeout_s: float,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> RunRecord:
    """Wait for the run's turn (cancelling it at `timeout_s`) and record how it ended."""
    timed_out = False
    try:
        outcome = await asyncio.wait_for(turn.wait(), timeout_s)
    except TimeoutError:
        turn.cancel()
        outcome = await turn.wait()
        # It may have ended on its own just as time ran out.
        timed_out = outcome.status == "cancelled"
    status, detail = _ended(outcome, timed_out)
    finished = await app_state.routine_store.update_run(
        run.id, run.owner_user_id, {"status": status, "detail": detail, "finished_at": clock()}
    )
    return finished or run

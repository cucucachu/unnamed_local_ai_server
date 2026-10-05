"""Starting a routine's run (M17-02 run-now; the M17-04 scheduler reuses this).

A run is a fresh thread (`routine_id` set) whose first user message is the
routine's prompt, run headlessly by the detached turn runner (M17-01) as
the routine's owner.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from langchain_core.messages import HumanMessage

from app.agent.turn_runner import ActiveTurn, TurnRequest
from app.core.delegation import Delegation
from app.db.routines import RoutineRecord
from app.db.threads import ThreadRecord
from app.routines.schedule import zone


def run_title(routine: RoutineRecord, started: datetime) -> str:
    local = started.astimezone(zone(routine.timezone))
    return f"{routine.name} · {local:%b} {local.day}"


def run_message(routine: RoutineRecord) -> str:
    return f'Routine "{routine.name}" (space {routine.space}):\n\n{routine.prompt}'


async def create_run_thread(app_state: Any, routine: RoutineRecord) -> ThreadRecord:
    return await app_state.thread_store.create(
        routine.owner_user_id, run_title(routine, datetime.now(UTC)), routine.id
    )


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
        )
    )
    turn.detach(queue)
    await app_state.routine_store.update(
        routine.id, routine.owner_user_id, {"last_run_at": datetime.now(UTC)}
    )
    return turn

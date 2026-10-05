"""Saving routines: shared by `/api/routines` (M17-02) and the agent's routine tools (M17-07).

A routine runs as its owner in a space they may edit (`editor` or
`owner`), asked of the platform for the caller: a person's identity token
from the app, or a chat turn's delegation from the agent (`Caller`). An
enabled routine holds a platform routine grant (M17-03) for its scheduled
runs: issued when it's created or enabled or moves space, revoked when it's
disabled or deleted. Failures are `RoutineError`s carrying an HTTP status.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from app.core.delegation import Caller, DelegationDenied, DelegationUnavailable
from app.db.routines import ApprovalMode, NewRoutine, RoutineRecord
from app.routines import schedule as sched

EDIT_ROLES = frozenset({"editor", "owner"})
MAX_GRANT_LABEL = 64


class RoutineError(Exception):
    def __init__(self, status: int, detail: str) -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail


@dataclass(frozen=True)
class Draft:
    name: str
    prompt: str
    space: str
    schedule: sched.Schedule
    timezone: str
    enabled: bool = True
    approval_mode: ApprovalMode = "ask"


def _platform_error(exc: DelegationDenied | DelegationUnavailable) -> RoutineError:
    if isinstance(exc, DelegationDenied):
        return RoutineError(401, "unauthenticated")
    return RoutineError(503, "platform unavailable")


async def editable_space(state: Any, caller: Caller, space: str) -> str:
    """`space` canonicalized, if the caller may edit it."""
    try:
        access = await state.delegation_client.space_role(caller, space)
    except (DelegationDenied, DelegationUnavailable) as exc:
        raise _platform_error(exc) from exc
    if access is None:
        raise RoutineError(422, f"unknown space: {space}")
    canonical, role = access
    if role not in EDIT_ROLES:
        raise RoutineError(403, "routines need edit rights on the space")
    return canonical


async def _issue_grant(state: Any, caller: Caller, routine_id: str, space: str, name: str) -> str:
    """A fresh grant for the routine's unattended runs; the platform drops any older one."""
    try:
        grant = await state.delegation_client.issue_routine_grant(
            caller, routine_id, space, f"Routine: {name}"[:MAX_GRANT_LABEL]
        )
    except (DelegationDenied, DelegationUnavailable) as exc:
        raise _platform_error(exc) from exc
    if grant is None:
        raise RoutineError(403, "routines need edit rights on the space")
    return grant


async def _revoke_grant(state: Any, grant: str) -> None:
    try:
        await state.delegation_client.revoke_routine_grant(grant)
    except (DelegationDenied, DelegationUnavailable) as exc:
        raise RoutineError(503, "platform unavailable") from exc


def check_timezone(timezone: str) -> None:
    try:
        sched.zone(timezone)
    except ValueError as exc:
        raise RoutineError(422, str(exc)) from exc


def next_run_at(schedule: dict, timezone: str, enabled: bool, now: datetime) -> datetime | None:
    if not enabled:
        return None
    next_at = sched.next_run(sched.parse_schedule(schedule), timezone, now)
    if next_at is None:
        raise RoutineError(422, "the schedule has no future run")
    return next_at


async def create(
    state: Any, caller: Caller, owner_user_id: str, draft: Draft, now: datetime
) -> RoutineRecord:
    check_timezone(draft.timezone)
    space = await editable_space(state, caller, draft.space)
    schedule = sched.dump_schedule(draft.schedule)
    store = state.routine_store
    record = await store.create(
        owner_user_id,
        NewRoutine(
            space=space,
            name=draft.name,
            prompt=draft.prompt,
            schedule=schedule,
            timezone=draft.timezone,
            enabled=draft.enabled,
            next_run_at=next_run_at(schedule, draft.timezone, draft.enabled, now),
            approval_mode=draft.approval_mode,
        ),
    )
    if record.enabled:
        try:
            grant = await _issue_grant(state, caller, record.id, space, record.name)
        except RoutineError:
            await store.delete(record.id, owner_user_id)
            raise
        record = await store.update(record.id, owner_user_id, {"grant_token": grant})
    return record


async def update(
    state: Any, caller: Caller, current: RoutineRecord, changes: dict, now: datetime
) -> RoutineRecord:
    """`changes`: any of `Draft`'s fields (a `schedule` already a `sched.Schedule`)."""
    changes = {k: v for k, v in changes.items() if v is not None}
    if "timezone" in changes:
        check_timezone(changes["timezone"])
    if "space" in changes:
        changes["space"] = await editable_space(state, caller, changes["space"])
    if "schedule" in changes:
        changes["schedule"] = sched.dump_schedule(changes["schedule"])
    if {"schedule", "timezone", "enabled"} & changes.keys():
        changes["next_run_at"] = next_run_at(
            changes.get("schedule", current.schedule),
            changes.get("timezone", current.timezone),
            changes.get("enabled", current.enabled),
            now,
        )
    enabled = changes.get("enabled", current.enabled)
    space = changes.get("space", current.space)
    if enabled and (not current.enabled or space != current.space or not current.grant_token):
        name = changes.get("name", current.name)
        changes["grant_token"] = await _issue_grant(state, caller, current.id, space, name)
    elif not enabled and current.grant_token:
        await _revoke_grant(state, current.grant_token)
        changes["grant_token"] = None
    record = await state.routine_store.update(current.id, current.owner_user_id, changes)
    if record is None:
        raise RoutineError(404, f"routine '{current.id}' not found")
    return record


async def delete(state: Any, current: RoutineRecord) -> None:
    if current.grant_token:
        await _revoke_grant(state, current.grant_token)
    if not await state.routine_store.delete(current.id, current.owner_user_id):
        raise RoutineError(404, f"routine '{current.id}' not found")

"""`/api/routines`: saved prompts the agent runs later (M17-02).

A routine runs as its owner in one of their spaces; saving one there needs
`editor` or `owner` (asked of the platform with the caller's identity
token). `schedule`/`timezone` drive `next_run_at` (see
`app.routines.schedule`); the scheduler (M17-04) fires due routines, and
`POST /routines/{id}/run` starts a run now. `GET /routines/{id}/runs` lists
its run records (status, timing, and the thread each one ran in).

An enabled routine holds a platform routine grant (M17-03) for its
scheduled runs: issued from the caller's identity when the routine is
created or enabled or moves space, revoked when it's disabled or deleted.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator

from app.core.delegation import Delegation, DelegationDenied, DelegationUnavailable
from app.core.identity import IDENTITY_HEADER, CurrentUser
from app.db.routines import NewRoutine, RoutineRecord, RoutineStore, RunRecord
from app.routines import schedule as sched
from app.routines.runs import create_run_thread

router = APIRouter()

Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]
Prompt = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=20_000)]
SpacePath = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=256)]
EDIT_ROLES = frozenset({"editor", "owner"})
MAX_GRANT_LABEL = 64


def _check_timezone(value: str | None) -> str | None:
    if value is not None:
        sched.zone(value)
    return value


class RoutineIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: Name
    prompt: Prompt
    space: SpacePath = "/personal"
    schedule: sched.Schedule
    timezone: Annotated[str, Field(max_length=64)]
    enabled: bool = True

    _tz = field_validator("timezone")(_check_timezone)


class RoutinePatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: Name | None = None
    prompt: Prompt | None = None
    space: SpacePath | None = None
    schedule: sched.Schedule | None = None
    timezone: Annotated[str, Field(max_length=64)] | None = None
    enabled: bool | None = None

    _tz = field_validator("timezone")(_check_timezone)


class RoutineOut(BaseModel):
    id: str
    name: str
    prompt: str
    space: str
    schedule: dict
    timezone: str
    enabled: bool
    next_run_at: datetime | None
    last_run_at: datetime | None
    created_at: datetime
    updated_at: datetime


class RunOut(BaseModel):
    id: str
    trigger: str
    status: str
    detail: str | None
    thread_id: str | None
    due_at: datetime | None
    started_at: datetime | None
    finished_at: datetime | None
    created_at: datetime


def _store(request: Request) -> RoutineStore:
    return request.app.state.routine_store


def _out(record: RoutineRecord) -> RoutineOut:
    return RoutineOut(**{name: getattr(record, name) for name in RoutineOut.model_fields})


def _run_out(record: RunRecord) -> RunOut:
    return RunOut(**{name: getattr(record, name) for name in RunOut.model_fields})


def _now() -> datetime:
    return datetime.now(UTC)


async def _owned(request: Request, routine_id: str, user_id: str) -> RoutineRecord:
    record = await _store(request).get(routine_id, user_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"routine '{routine_id}' not found")
    return record


async def _editable_space(request: Request, space: str) -> str:
    """`space` canonicalized, if the caller may edit it; else 403/422."""
    client = request.app.state.delegation_client
    try:
        access = await client.space_role(request.headers.get(IDENTITY_HEADER) or "", space)
    except DelegationDenied as exc:
        raise HTTPException(status_code=401, detail="unauthenticated") from exc
    except DelegationUnavailable as exc:
        raise HTTPException(status_code=503, detail="platform unavailable") from exc
    if access is None:
        raise HTTPException(status_code=422, detail=f"unknown space: {space}")
    canonical, role = access
    if role not in EDIT_ROLES:
        raise HTTPException(status_code=403, detail="routines need edit rights on the space")
    return canonical


def _platform_error(exc: DelegationDenied | DelegationUnavailable) -> HTTPException:
    if isinstance(exc, DelegationDenied):
        return HTTPException(status_code=401, detail="unauthenticated")
    return HTTPException(status_code=503, detail="platform unavailable")


async def _issue_grant(request: Request, routine_id: str, space: str, name: str) -> str:
    """A fresh grant for the routine's unattended runs; the platform drops any older one."""
    client = request.app.state.delegation_client
    try:
        grant = await client.issue_routine_grant(
            request.headers.get(IDENTITY_HEADER) or "",
            routine_id,
            space,
            f"Routine: {name}"[:MAX_GRANT_LABEL],
        )
    except (DelegationDenied, DelegationUnavailable) as exc:
        raise _platform_error(exc) from exc
    if grant is None:
        raise HTTPException(status_code=403, detail="routines need edit rights on the space")
    return grant


async def _revoke_grant(request: Request, grant: str) -> None:
    try:
        await request.app.state.delegation_client.revoke_routine_grant(grant)
    except (DelegationDenied, DelegationUnavailable) as exc:
        raise HTTPException(status_code=503, detail="platform unavailable") from exc


def _next_run_at(schedule: dict, timezone: str, enabled: bool) -> datetime | None:
    if not enabled:
        return None
    next_at = sched.next_run(sched.parse_schedule(schedule), timezone, _now())
    if next_at is None:
        raise HTTPException(status_code=422, detail="the schedule has no future run")
    return next_at


@router.get("/routines", response_model=list[RoutineOut])
async def list_routines(request: Request, user: CurrentUser) -> list[RoutineOut]:
    return [_out(r) for r in await _store(request).list_for_owner(user.user_id)]


@router.post("/routines", status_code=201, response_model=RoutineOut)
async def create_routine(body: RoutineIn, request: Request, user: CurrentUser) -> RoutineOut:
    space = await _editable_space(request, body.space)
    schedule = sched.dump_schedule(body.schedule)
    record = await _store(request).create(
        user.user_id,
        NewRoutine(
            space=space,
            name=body.name,
            prompt=body.prompt,
            schedule=schedule,
            timezone=body.timezone,
            enabled=body.enabled,
            next_run_at=_next_run_at(schedule, body.timezone, body.enabled),
        ),
    )
    if record.enabled:
        try:
            grant = await _issue_grant(request, record.id, space, record.name)
        except HTTPException:
            await _store(request).delete(record.id, user.user_id)
            raise
        record = await _store(request).update(record.id, user.user_id, {"grant_token": grant})
    return _out(record)


@router.get("/routines/{routine_id}", response_model=RoutineOut)
async def get_routine(routine_id: str, request: Request, user: CurrentUser) -> RoutineOut:
    return _out(await _owned(request, routine_id, user.user_id))


@router.patch("/routines/{routine_id}", response_model=RoutineOut)
async def update_routine(
    routine_id: str, body: RoutinePatch, request: Request, user: CurrentUser
) -> RoutineOut:
    current = await _owned(request, routine_id, user.user_id)
    changes = body.model_dump(exclude_unset=True)
    if changes.get("space") is not None:
        changes["space"] = await _editable_space(request, changes["space"])
    if body.schedule is not None:
        changes["schedule"] = sched.dump_schedule(body.schedule)
    changes = {k: v for k, v in changes.items() if v is not None}
    if {"schedule", "timezone", "enabled"} & changes.keys():
        changes["next_run_at"] = _next_run_at(
            changes.get("schedule", current.schedule),
            changes.get("timezone", current.timezone),
            changes.get("enabled", current.enabled),
        )
    enabled = changes.get("enabled", current.enabled)
    space = changes.get("space", current.space)
    if enabled and (not current.enabled or space != current.space or not current.grant_token):
        name = changes.get("name", current.name)
        changes["grant_token"] = await _issue_grant(request, routine_id, space, name)
    elif not enabled and current.grant_token:
        await _revoke_grant(request, current.grant_token)
        changes["grant_token"] = None
    record = await _store(request).update(routine_id, user.user_id, changes)
    if record is None:
        raise HTTPException(status_code=404, detail=f"routine '{routine_id}' not found")
    return _out(record)


@router.delete("/routines/{routine_id}", status_code=204)
async def delete_routine(routine_id: str, request: Request, user: CurrentUser) -> None:
    current = await _owned(request, routine_id, user.user_id)
    if current.grant_token:
        await _revoke_grant(request, current.grant_token)
    if not await _store(request).delete(routine_id, user.user_id):
        raise HTTPException(status_code=404, detail=f"routine '{routine_id}' not found")


@router.post("/routines/{routine_id}/run", status_code=202, response_model=RunOut)
async def run_routine(routine_id: str, request: Request, user: CurrentUser) -> RunOut:
    """Start a run now, as the caller (whose session the run's delegation hangs off)."""
    routine = await _owned(request, routine_id, user.user_id)
    await _editable_space(request, routine.space)
    thread_store = request.app.state.thread_store
    thread = await create_run_thread(request.app.state, routine)
    try:
        delegation = await Delegation.obtain(
            request.app.state.delegation_client,
            request.headers.get(IDENTITY_HEADER) or "",
            thread.id,
        )
    except (DelegationDenied, DelegationUnavailable) as exc:
        await thread_store.delete(thread.id, user.user_id)
        if isinstance(exc, DelegationDenied):
            raise HTTPException(status_code=401, detail="unauthenticated") from exc
        raise HTTPException(status_code=503, detail="platform unavailable") from exc
    run = await request.app.state.routine_scheduler.start_manual(routine, thread, delegation)
    return _run_out(run)


@router.get("/routines/{routine_id}/runs", response_model=list[RunOut])
async def list_runs(routine_id: str, request: Request, user: CurrentUser) -> list[RunOut]:
    await _owned(request, routine_id, user.user_id)
    return [_run_out(r) for r in await _store(request).list_runs(routine_id, user.user_id)]

"""`/api/routines`: saved prompts the agent runs later (M17-02).

A routine runs as its owner in one of their spaces; saving one there needs
`editor` or `owner` (asked of the platform with the caller's identity
token). `schedule`/`timezone` drive `next_run_at` (see
`app.routines.schedule`); the scheduler (M17-04) fires due routines, and
`POST /routines/{id}/run` starts a run now. Runs are threads with
`routine_id` set, listed by `GET /routines/{id}/runs`.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator

from app.api.chat import ThreadOut, _to_thread_out
from app.core.delegation import Delegation, DelegationDenied, DelegationUnavailable
from app.core.identity import IDENTITY_HEADER, CurrentUser
from app.db.routines import NewRoutine, RoutineRecord, RoutineStore
from app.routines import schedule as sched
from app.routines.runs import create_run_thread, launch_run

router = APIRouter()

Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]
Prompt = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=20_000)]
SpacePath = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=256)]
EDIT_ROLES = frozenset({"editor", "owner"})


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
    thread_id: str


def _store(request: Request) -> RoutineStore:
    return request.app.state.routine_store


def _out(record: RoutineRecord) -> RoutineOut:
    return RoutineOut(**{name: getattr(record, name) for name in RoutineOut.model_fields})


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
    record = await _store(request).update(routine_id, user.user_id, changes)
    if record is None:
        raise HTTPException(status_code=404, detail=f"routine '{routine_id}' not found")
    return _out(record)


@router.delete("/routines/{routine_id}", status_code=204)
async def delete_routine(routine_id: str, request: Request, user: CurrentUser) -> None:
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
    await launch_run(request.app.state, routine, thread, delegation)
    return RunOut(thread_id=thread.id)


@router.get("/routines/{routine_id}/runs", response_model=list[ThreadOut])
async def list_runs(routine_id: str, request: Request, user: CurrentUser) -> list[ThreadOut]:
    await _owned(request, routine_id, user.user_id)
    runs = await request.app.state.thread_store.list_for_owner(user.user_id, routine_id=routine_id)
    return [_to_thread_out(r) for r in runs]

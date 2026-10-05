"""`/api/routines`: saved prompts the agent runs later (M17-02).

A routine runs as its owner in one of their spaces; saving one there needs
`editor` or `owner` (asked of the platform with the caller's identity
token). `schedule`/`timezone` drive `next_run_at` (see
`app.routines.schedule`); the scheduler (M17-04) fires due routines, and
`POST /routines/{id}/run` starts a run now. `GET /routines/{id}/runs` lists
its run records (status, timing, and the thread each one ran in); a
routine's `last_run` (M17-06) is the newest of them.

Saving goes through `app.routines.service` (shared with the agent's
routine tools), on the caller's identity token: the space check and the
routine grant (M17-03) an enabled routine holds for its scheduled runs.

Its `approval_mode` (M17-05, `app.agent.approvals`) says what its runs may
do without asking. `GET /inbox` lists the caller's ended and paused runs,
newest first, with an unread count; `POST /inbox/read` marks them read (a
run is unread again whenever its status changes).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator

from app.core.delegation import Caller, Delegation, DelegationDenied, DelegationUnavailable
from app.core.identity import IDENTITY_HEADER, CurrentUser
from app.db.routines import ApprovalMode, RoutineRecord, RoutineStore, RunRecord
from app.routines import schedule as sched
from app.routines import service
from app.routines.runs import create_run_thread

router = APIRouter()

Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]
Prompt = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=20_000)]
SpacePath = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=256)]


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
    approval_mode: ApprovalMode = "ask"

    _tz = field_validator("timezone")(_check_timezone)


class RoutinePatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: Name | None = None
    prompt: Prompt | None = None
    space: SpacePath | None = None
    schedule: sched.Schedule | None = None
    timezone: Annotated[str, Field(max_length=64)] | None = None
    enabled: bool | None = None
    approval_mode: ApprovalMode | None = None

    _tz = field_validator("timezone")(_check_timezone)


class LastRun(BaseModel):
    id: str
    status: str
    finished_at: datetime | None
    thread_id: str | None


class RoutineOut(BaseModel):
    id: str
    name: str
    prompt: str
    space: str
    schedule: dict
    timezone: str
    enabled: bool
    approval_mode: str
    next_run_at: datetime | None
    last_run_at: datetime | None
    created_at: datetime
    updated_at: datetime
    last_run: LastRun | None = None


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


def _out(record: RoutineRecord, last_run: RunRecord | None = None) -> RoutineOut:
    fields = {name: getattr(record, name) for name in RoutineOut.model_fields if name != "last_run"}
    if last_run is not None:
        fields["last_run"] = LastRun(
            **{name: getattr(last_run, name) for name in LastRun.model_fields}
        )
    return RoutineOut(**fields)


def _run_out(record: RunRecord) -> RunOut:
    return RunOut(**{name: getattr(record, name) for name in RunOut.model_fields})


def _now() -> datetime:
    return datetime.now(UTC)


async def _owned(request: Request, routine_id: str, user_id: str) -> RoutineRecord:
    record = await _store(request).get(routine_id, user_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"routine '{routine_id}' not found")
    return record


def _caller(request: Request) -> Caller:
    return Caller(identity_token=request.headers.get(IDENTITY_HEADER) or "")


def _http(exc: service.RoutineError) -> HTTPException:
    return HTTPException(status_code=exc.status, detail=exc.detail)


@router.get("/routines", response_model=list[RoutineOut])
async def list_routines(request: Request, user: CurrentUser) -> list[RoutineOut]:
    store = _store(request)
    latest = await store.latest_runs(user.user_id)
    return [_out(r, latest.get(r.id)) for r in await store.list_for_owner(user.user_id)]


@router.post("/routines", status_code=201, response_model=RoutineOut)
async def create_routine(body: RoutineIn, request: Request, user: CurrentUser) -> RoutineOut:
    draft = service.Draft(**body.model_dump(exclude={"schedule"}), schedule=body.schedule)
    try:
        record = await service.create(
            request.app.state, _caller(request), user.user_id, draft, _now()
        )
    except service.RoutineError as exc:
        raise _http(exc) from exc
    return _out(record)


@router.get("/routines/{routine_id}", response_model=RoutineOut)
async def get_routine(routine_id: str, request: Request, user: CurrentUser) -> RoutineOut:
    record = await _owned(request, routine_id, user.user_id)
    runs = await _store(request).list_runs(routine_id, user.user_id)
    return _out(record, runs[0] if runs else None)


@router.patch("/routines/{routine_id}", response_model=RoutineOut)
async def update_routine(
    routine_id: str, body: RoutinePatch, request: Request, user: CurrentUser
) -> RoutineOut:
    current = await _owned(request, routine_id, user.user_id)
    changes = body.model_dump(exclude_unset=True, exclude={"schedule"})
    if body.schedule is not None:
        changes["schedule"] = body.schedule
    try:
        record = await service.update(request.app.state, _caller(request), current, changes, _now())
    except service.RoutineError as exc:
        raise _http(exc) from exc
    return _out(record)


@router.delete("/routines/{routine_id}", status_code=204)
async def delete_routine(routine_id: str, request: Request, user: CurrentUser) -> None:
    current = await _owned(request, routine_id, user.user_id)
    try:
        await service.delete(request.app.state, current)
    except service.RoutineError as exc:
        raise _http(exc) from exc


@router.post("/routines/{routine_id}/run", status_code=202, response_model=RunOut)
async def run_routine(routine_id: str, request: Request, user: CurrentUser) -> RunOut:
    """Start a run now, as the caller (whose session the run's delegation hangs off)."""
    routine = await _owned(request, routine_id, user.user_id)
    try:
        await service.editable_space(request.app.state, _caller(request), routine.space)
    except service.RoutineError as exc:
        raise _http(exc) from exc
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


INBOX_LIMIT = 50


class InboxItem(RunOut):
    routine_id: str
    routine_name: str
    unread: bool


class Inbox(BaseModel):
    unread: int
    items: list[InboxItem]


class InboxRead(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # None: everything.
    run_ids: list[Annotated[str, Field(max_length=64)]] | None = Field(default=None, max_length=500)


async def _inbox(request: Request, user_id: str) -> Inbox:
    store = _store(request)
    items = [
        InboxItem(
            **_run_out(run).model_dump(),
            routine_id=run.routine_id,
            routine_name=name,
            unread=run.seen_at is None,
        )
        for run, name in await store.inbox(user_id, INBOX_LIMIT)
    ]
    return Inbox(unread=await store.unread_count(user_id), items=items)


@router.get("/inbox", response_model=Inbox)
async def get_inbox(request: Request, user: CurrentUser) -> Inbox:
    return await _inbox(request, user.user_id)


@router.post("/inbox/read", response_model=Inbox)
async def mark_inbox_read(body: InboxRead, request: Request, user: CurrentUser) -> Inbox:
    await _store(request).mark_seen(user.user_id, body.run_ids, _now())
    return await _inbox(request, user.user_id)

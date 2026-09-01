"""The four §7 endpoints - thin HTTP adapters over `app.sessions.SessionManager`.

Every field of the actual container spec is hardcoded in
`app.sessions.build_run_kwargs`; nothing here ever forwards a request body
value into a container parameter (README.md "Isolation boundary") - the
only caller-controlled values that reach Docker at all are the `session_id`
(validated by the `Path(pattern=...)` below before any handler body runs)
and the `command` string passed to `exec_run`.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Path, Request
from pydantic import BaseModel

from app.sessions import SESSION_ID_PATTERN

router = APIRouter()

SessionId = Annotated[str, Path(pattern=SESSION_ID_PATTERN)]
"""Thread UUIDs qualify. A non-matching id never reaches a handler - FastAPI/
Pydantic reject it with `422` before routing, per §7."""


class EnsureResponse(BaseModel):
    container_id: str
    created: bool


class ExecuteRequest(BaseModel):
    command: str
    # `None` (rather than a static literal default) so the handler can fall
    # back to the *runtime* `settings.exec_default_timeout_s` - a Pydantic
    # field default is fixed at class-definition time and can't see
    # `Settings`, which itself is only resolved per-app (tests override it).
    timeout_seconds: int | None = None


class ExecuteResponse(BaseModel):
    stdout: str
    stderr: str
    exit_code: int
    timed_out: bool
    duration_ms: int
    truncated: bool


class SessionListEntry(BaseModel):
    session_id: str
    container_id: str
    last_used: datetime


@router.post("/sessions/{session_id}/ensure")
async def ensure_session(session_id: SessionId, request: Request) -> EnsureResponse:
    result = await request.app.state.session_manager.ensure(session_id)
    return EnsureResponse(**result)


@router.post("/sessions/{session_id}/execute")
async def execute_in_session(
    session_id: SessionId, body: ExecuteRequest, request: Request
) -> ExecuteResponse:
    settings = request.app.state.settings
    timeout_seconds = (
        body.timeout_seconds if body.timeout_seconds is not None else settings.exec_default_timeout_s
    )
    result = await request.app.state.session_manager.execute(session_id, body.command, timeout_seconds)
    return ExecuteResponse(
        stdout=result.stdout,
        stderr=result.stderr,
        exit_code=result.exit_code,
        timed_out=result.timed_out,
        duration_ms=result.duration_ms,
        truncated=result.truncated,
    )


@router.delete("/sessions/{session_id}", status_code=204)
async def delete_session(session_id: SessionId, request: Request) -> None:
    await request.app.state.session_manager.remove(session_id)


@router.get("/sessions")
async def list_sessions(request: Request) -> list[SessionListEntry]:
    sessions = await request.app.state.session_manager.list_sessions()
    return [
        SessionListEntry(session_id=s.session_id, container_id=s.container_id, last_used=s.last_used)
        for s in sessions
    ]

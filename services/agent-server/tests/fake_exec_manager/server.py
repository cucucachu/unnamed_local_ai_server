"""ASGI app implementing the two code-exec-manager endpoints
`app.agent.execute_code_tool` calls (`POST /sessions/{id}/ensure`,
`POST /sessions/{id}/execute`) — a deterministic stand-in for the real
`services/code-exec-manager` service, bound to an ephemeral port by the
`fake_exec_manager` fixture (`tests/conftest.py`), same pattern as
`tests/fake_model/server.py`.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from .scripting import FakeExecManager


def create_fake_exec_manager_app(fake: FakeExecManager) -> FastAPI:
    app = FastAPI()

    @app.post("/sessions/{session_id}/ensure")
    async def ensure(session_id: str) -> Any:
        fake.record_ensure(session_id)
        return {"container_id": f"fake-container-{session_id}", "created": True}

    @app.post("/sessions/{session_id}/execute")
    async def execute(session_id: str, request: Request) -> Any:
        body = await request.json()
        fake.record_execute(session_id, body["command"], body["timeout_seconds"])
        return JSONResponse(fake.execute_response, status_code=fake.execute_status_code)

    return app

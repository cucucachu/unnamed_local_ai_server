"""Scriptable state for the fake code-exec-manager (`server.py`).

Records every `ensure`/`execute` call a test drives through
`app.agent.execute_code_tool`'s HTTP client, and lets a test configure what
`execute` responds with (success shape, or a non-2xx status to exercise the
tool's failure path) — mirrors `tests/fake_model/scripting.py`'s `FakeModel`
pattern (a plain, not-thread-safe, single-test-at-a-time recorder driving an
in-process ASGI app).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ExecuteCall:
    session_id: str
    command: str
    timeout_seconds: int


class FakeExecManager:
    def __init__(self) -> None:
        self.base_url: str = ""  # filled in by the `fake_exec_manager` fixture
        self.ensure_calls: list[str] = []
        self.execute_calls: list[ExecuteCall] = []

        # Matches `services/code-exec-manager/app/api.py`'s `ExecuteResponse`
        # shape exactly. Tests mutate this directly to script a response.
        self.execute_response: dict = {
            "stdout": "",
            "stderr": "",
            "exit_code": 0,
            "timed_out": False,
            "duration_ms": 1,
            "truncated": False,
        }
        self.execute_status_code: int = 200

    def record_ensure(self, session_id: str) -> None:
        self.ensure_calls.append(session_id)

    def record_execute(self, session_id: str, command: str, timeout_seconds: int) -> None:
        self.execute_calls.append(ExecuteCall(session_id, command, timeout_seconds))

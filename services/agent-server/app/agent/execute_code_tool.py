"""`execute_code`: the agent's hands into a sandboxed Linux container.

Session-scoped to the chat thread — each thread gets its own long-lived
exec-manager session/container (`app/agent/build.py`'s `build_agent` wires
the thread's `RunnableConfig` straight through to `config["configurable"]
["thread_id"]`, same as the checkpointer). This module only ever talks HTTP
to the code-exec-manager (`app.core.config.Settings.exec_manager_url`); it
never touches Docker directly (see `services/code-exec-manager/README.md`'s
"Isolation boundary" — this codebase's *only* docker.sock holder is that
separate service).
"""

from __future__ import annotations

import re

import httpx
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool

from app.core.config import Settings

_DISALLOWED_SESSION_ID_CHARS = re.compile(r"[^a-zA-Z0-9_-]")
_SESSION_ID_MAX_LEN = 64
"""Mirrors the exec-manager's own `SESSION_ID_PATTERN`
(`services/code-exec-manager/app/sessions.py`) — kept as a literal copy
rather than a cross-service import (these are two independently deployed
services, each built into its own Docker image)."""

_TIMEOUT_MIN_S = 1
_TIMEOUT_MAX_S = 600


def _sanitize_session_id(thread_id: str | None) -> str:
    """Coerce `thread_id` into the exec-manager's `^[a-zA-Z0-9_-]{1,64}$` shape.

    Thread ids in this codebase are already UUIDs, so this is normally a
    no-op — but the exec-manager 422s on any mismatch, so a missing/malformed
    id (empty string, `None`, stray punctuation, over-length) must never
    reach it unsanitized. Falls back to the literal `"default"` if nothing
    usable survives sanitization.
    """
    if not thread_id:
        return "default"
    sanitized = _DISALLOWED_SESSION_ID_CHARS.sub("", thread_id)[:_SESSION_ID_MAX_LEN]
    return sanitized or "default"


def _clamp_timeout(timeout_seconds: int) -> int:
    return max(_TIMEOUT_MIN_S, min(_TIMEOUT_MAX_S, timeout_seconds))


def _format_result(result: dict) -> str:
    timed_out_suffix = " (TIMED OUT)" if result["timed_out"] else ""
    stdout = result["stdout"] or "(empty)"
    stderr = result["stderr"] or "(empty)"
    formatted = (
        f"exit_code: {result['exit_code']}{timed_out_suffix}\n"
        f"--- stdout ---\n"
        f"{stdout}\n"
        f"--- stderr ---\n"
        f"{stderr}"
    )
    if result["truncated"]:
        formatted += "\n[output truncated]"
    return formatted


def make_execute_code_tool(settings: Settings):
    """Build the `execute_code` tool bound to one `Settings` instance.

    A factory rather than a single module-level tool bound to production
    `Settings()`: `build_agent(settings, checkpointer)` receives a distinct
    `Settings` per call (tests point `exec_manager_url` at a fake server per
    test), and a `@tool`-decorated function's own signature is fixed by the
    framework — it must match what the model can call (`command`/
    `timeout_seconds`) plus the framework-injected `config`, so `settings`
    can't be a real parameter of the tool function itself. Closing over it
    here is the cleanest way to make it available without a module global.
    """

    @tool
    async def execute_code(command: str, config: RunnableConfig, timeout_seconds: int = 120) -> str:
        """Run a shell command in a sandboxed Linux container.

        The container has NO network access. The user's workspace is mounted read-write at
        /workspace — the same tree file tools expose as / and as /workspace.
        Installed: Python 3 with pandas/numpy/pillow/matplotlib/openpyxl/pypdf, Node.js, git,
        ffmpeg, imagemagick, pandoc, ripgrep, jq. You cannot install packages. State in /tmp
        and $HOME is ephemeral; only /workspace persists. Long jobs: raise timeout_seconds
        (max 600).
        """
        thread_id = (config.get("configurable") or {}).get("thread_id")
        session_id = _sanitize_session_id(thread_id)
        clamped_timeout_seconds = _clamp_timeout(timeout_seconds)

        try:
            async with httpx.AsyncClient(base_url=settings.exec_manager_url) as client:
                ensure_response = await client.post(f"/sessions/{session_id}/ensure")
                ensure_response.raise_for_status()
                execute_response = await client.post(
                    f"/sessions/{session_id}/execute",
                    json={"command": command, "timeout_seconds": clamped_timeout_seconds},
                )
                execute_response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            return (
                f"execute_code failed: {exc.response.status_code} "
                f"{exc.response.text or exc.response.reason_phrase}"
            )
        except httpx.HTTPError as exc:
            return f"execute_code failed: {exc!r}"

        return _format_result(execute_response.json())

    return execute_code

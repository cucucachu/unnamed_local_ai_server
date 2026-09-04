"""The container lifecycle: `ensure` / `execute` / `remove` / `list_sessions`.

Implements docs/ARCHITECTURE.md's "Contracts" section's exec-container
hardening spec exactly - this module is the *only* place in the codebase
that builds a container-creation call, and every field of that call is a
hardcoded constant or derived from `Settings`, never from a caller-supplied
value (README.md "Isolation boundary": callers send a command string, never
a container spec).

Request-shape validation (the `session_id` regex → 422) lives at the API
layer (`app/api.py`, via FastAPI's own `Path(pattern=...)`), not here - this
module assumes it's only ever called with an already-valid session id and
concerns itself purely with the Docker side of the lifecycle.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import anyio.to_thread
import docker.errors
from fastapi import HTTPException

from app.core.config import Settings

logger = logging.getLogger(__name__)

SESSION_ID_PATTERN = r"^[a-zA-Z0-9_-]{1,64}$"

MAX_OUTPUT_BYTES = 200_000
"""stdout/stderr truncation limit, per §7 - applied independently to each."""

OUTER_TIMEOUT_GRACE_S = 15
"""Added to the caller's `timeout_seconds` for the outer `asyncio.wait_for`
guard - covers a hung Docker Engine API call, which GNU `timeout` running
*inside* the container can never protect against (it can only bound the
command it wraps, not the `docker exec` call itself)."""

GNU_TIMEOUT_EXIT_CODE = 124
"""GNU coreutils `timeout`'s documented exit code when it kills the wrapped
command for exceeding the wall clock - the signal this module maps to
`timed_out=True` while still reporting `exit_code=124` (per §7, the code is
kept, not replaced)."""


def container_name(session_id: str) -> str:
    """`homeai-exec-{session_id}` - the one place this naming is computed,
    shared by every lifecycle method so it can never drift between them."""
    return f"homeai-exec-{session_id}"


def build_run_kwargs(session_id: str, settings: Settings) -> dict[str, Any]:
    """The exact §7 hardening spec for a fresh exec container, as a plain
    dict ready to splat into `docker_client.containers.run(**kwargs)`.

    Deliberately a pure function (no docker client, no I/O, no `self`) so
    the security-critical unit test can assert it field-by-field in
    isolation, independent of `SessionManager`'s own control flow.
    """
    return {
        "image": settings.toolbox_image,
        "name": container_name(session_id),
        "command": ["sleep", "infinity"],
        "detach": True,
        "network_mode": "none",
        "cap_drop": ["ALL"],
        "security_opt": ["no-new-privileges"],
        "read_only": True,
        # `/tmp` needs no explicit `uid`/`gid`/`mode`: Docker already mounts
        # bare `--tmpfs <path>:size=...` targets named `/tmp` as `1777`
        # (world-writable+sticky), same as a normal host `/tmp` - verified
        # via `docker run ... --tmpfs /tmp:size=512m stat /tmp`. `/home/
        # homeai` gets no such special-cased default: verified the same way
        # it mounts `0750 root:root`, which the non-root `user=` below can't
        # even traverse - explicit `uid`/`gid`/`mode` here isn't a deviation
        # from §7's given `tmpfs` value (the size is unchanged), just the
        # minimum addition needed for the mount to be usable at all by the
        # UID it's actually mounted for.
        "tmpfs": {
            "/tmp": "size=512m",
            "/home/homeai": (
                f"size=64m,uid={settings.homeai_uid},gid={settings.homeai_gid},mode=0700"
            ),
        },
        "mem_limit": "4g",
        "nano_cpus": 4_000_000_000,
        "user": f"{settings.homeai_uid}:{settings.homeai_gid}",
        "pids_limit": 512,
        "volumes": {settings.workspace_host_dir: {"bind": "/workspace", "mode": "rw"}},
        "labels": {"homeai.exec": "1", "homeai.session": session_id},
    }


@dataclass(frozen=True)
class ExecResult:
    stdout: str
    stderr: str
    exit_code: int
    timed_out: bool
    duration_ms: int
    truncated: bool


@dataclass(frozen=True)
class SessionInfo:
    session_id: str
    container_id: str
    last_used: datetime


class SessionManager:
    """Owns every Docker interaction for exec sessions.

    `docker_client` is injected (not `docker.from_env()`'d internally) so
    unit tests can pass a stub implementing just the subset of the real
    `docker.DockerClient` surface this class actually calls, without a real
    Docker daemon.

    `last_used` is in-process state only (a plain instance dict, not
    persisted) - per the ticket, M4-03's idle reaper reads it, and losing it
    across a code-exec-manager restart is acceptable (worst case: a
    still-running container looks freshly-used again until its next
    `ensure`/`execute`).
    """

    def __init__(self, docker_client: Any, settings: Settings) -> None:
        self._client = docker_client
        self._settings = settings
        self._last_used: dict[str, datetime] = {}

    def _touch(self, session_id: str) -> None:
        self._last_used[session_id] = datetime.now(UTC)

    async def ensure(self, session_id: str) -> dict[str, Any]:
        return await anyio.to_thread.run_sync(self._ensure_sync, session_id)

    def _ensure_sync(self, session_id: str) -> dict[str, Any]:
        name = container_name(session_id)
        try:
            container = self._client.containers.get(name)
        except docker.errors.NotFound:
            container = None

        if container is not None:
            container.reload()
            if container.status == "running":
                self._touch(session_id)
                return {"container_id": container.id, "created": False}
            # Stopped/exited: remove and recreate fresh rather than
            # `container.start()`-ing the old one. A bare restart wouldn't
            # re-apply the §7 spec if it's ever changed (e.g. a
            # code-exec-manager upgrade landing new hardening flags), and
            # "if absent/stopped, create+start" is what the ticket asks for
            # literally - both branches funnel into the same fresh create.
            container.remove(force=True)

        container = self._client.containers.run(**build_run_kwargs(session_id, self._settings))
        self._touch(session_id)
        return {"container_id": container.id, "created": True}

    async def execute(self, session_id: str, command: str, timeout_seconds: int) -> ExecResult:
        name = container_name(session_id)
        try:
            container = await anyio.to_thread.run_sync(self._client.containers.get, name)
        except docker.errors.NotFound:
            raise HTTPException(
                404, f"session not found: {session_id!r} (call ensure first)"
            ) from None

        self._touch(session_id)
        start = time.monotonic()
        try:
            exit_code, stdout, stderr, out_truncated = await asyncio.wait_for(
                anyio.to_thread.run_sync(self._exec_sync, container, command, timeout_seconds),
                timeout=timeout_seconds + OUTER_TIMEOUT_GRACE_S,
            )
            timed_out = exit_code == GNU_TIMEOUT_EXIT_CODE
        except TimeoutError:
            # The outer guard fired, meaning the Docker Engine API call
            # itself hung past even the GNU-`timeout`-wrapped command's own
            # deadline plus grace - GNU `timeout` inside the container can
            # never protect against THIS, only against the wrapped command
            # overrunning. Kill+remove so a wedged exec doesn't leave a
            # zombie container permanently squatting on the session's name.
            await anyio.to_thread.run_sync(self._force_remove, container)
            exit_code, stdout, stderr, out_truncated, timed_out = -1, "", "", False, True
        duration_ms = int((time.monotonic() - start) * 1000)

        return ExecResult(
            stdout=stdout,
            stderr=stderr,
            exit_code=exit_code,
            timed_out=timed_out,
            duration_ms=duration_ms,
            truncated=out_truncated,
        )

    def _exec_sync(
        self, container: Any, command: str, timeout_seconds: int
    ) -> tuple[int, str, str, bool]:
        # GNU `timeout` bounds the wall clock *inside* the container; the
        # outer `asyncio.wait_for` around this whole call (see `execute`)
        # bounds the Docker Engine API call itself, which `timeout` has no
        # visibility into.
        wrapped = [
            "timeout",
            "--signal=TERM",
            "--kill-after=5",
            f"{timeout_seconds}s",
            "bash",
            "-lc",
            command,
        ]
        result = container.exec_run(
            wrapped,
            demux=True,
            user=f"{self._settings.homeai_uid}:{self._settings.homeai_gid}",
        )
        stdout_bytes, stderr_bytes = result.output
        stdout_bytes, stdout_truncated = _truncate_bytes(stdout_bytes)
        stderr_bytes, stderr_truncated = _truncate_bytes(stderr_bytes)
        return (
            result.exit_code,
            stdout_bytes.decode("utf-8", errors="replace"),
            stderr_bytes.decode("utf-8", errors="replace"),
            stdout_truncated or stderr_truncated,
        )

    def _force_remove(self, container: Any) -> None:
        try:
            container.kill()
        except docker.errors.APIError:
            pass
        try:
            container.remove(force=True)
        except docker.errors.APIError:
            pass

    async def remove(self, session_id: str) -> None:
        name = container_name(session_id)
        await anyio.to_thread.run_sync(self._remove_sync, name)
        self._last_used.pop(session_id, None)

    def _remove_sync(self, name: str) -> None:
        try:
            container = self._client.containers.get(name)
        except docker.errors.NotFound:
            return  # idempotent - already gone
        try:
            container.stop(timeout=5)
        except docker.errors.APIError:
            pass  # already stopped/stopping - `remove` below still applies
        container.remove(force=True)

    async def list_sessions(self) -> list[SessionInfo]:
        containers = await anyio.to_thread.run_sync(self._list_sync)
        sessions = []
        for container in containers:
            session_id = container.labels.get("homeai.session")
            if not session_id:
                continue  # not one of ours (defensive; the label filter below already scopes this)
            last_used = self._last_used.get(session_id) or datetime.now(UTC)
            sessions.append(
                SessionInfo(session_id=session_id, container_id=container.id, last_used=last_used)
            )
        return sessions

    def _list_sync(self) -> list[Any]:
        return self._client.containers.list(all=True, filters={"label": "homeai.exec=1"})

    async def reap_once(self, now: datetime | None = None) -> None:
        """One idle-reaper pass: stop+remove every `homeai.exec=1` container
        (running or stopped - hence `all=True` in `_list_sync`) whose idle
        time exceeds `settings.exec_idle_minutes`.

        `now` is injectable (defaults to `datetime.now(UTC)`) so
        `app/reaper.py`'s 60s-interval background loop never needs it, while
        unit tests can drive the idle clock deterministically without real
        sleeping.

        Never raises - a listing failure (e.g. a transient Docker API error)
        or a single container's own processing failure is logged and
        swallowed, so `app/reaper.py`'s `while True` loop keeps ticking on
        the next 60s tick either way.
        """
        now = now or datetime.now(UTC)
        await anyio.to_thread.run_sync(self._reap_once_sync, now)

    def _reap_once_sync(self, now: datetime) -> None:
        try:
            containers = self._list_sync()
        except Exception:
            logger.exception("reaper: failed to list homeai.exec containers")
            return

        for container in containers:
            try:
                self._reap_container_if_idle(container, now)
            except Exception:
                logger.exception(
                    "reaper: failed to process container %r", getattr(container, "name", "<unknown>")
                )

    def _reap_container_if_idle(self, container: Any, now: datetime) -> None:
        session_id = container.labels.get("homeai.session")
        if not session_id:
            return  # not one of ours (defensive; the label filter already scopes this)

        last_used = self._last_used.get(session_id)
        if last_used is None:
            # Unknown to this process - either a genuinely fresh container
            # this instance itself just created (impossible in practice:
            # `ensure`/`execute` always `_touch` first) or, per the ticket,
            # a manager restart that wiped `_last_used` while the container
            # kept running. Adopt it by seeding its idle clock from Docker's
            # own record of when it started, rather than treating "unknown"
            # as "just used" (which would mean a restart resets the idle
            # clock forever and nothing ever gets reaped).
            last_used = self._adopt(session_id, container)

        idle_minutes = (now - last_used).total_seconds() / 60
        if idle_minutes <= self._settings.exec_idle_minutes:
            return

        logger.info(
            "reaper: removing idle session %r (idle %.1f min > limit %d min)",
            session_id,
            idle_minutes,
            self._settings.exec_idle_minutes,
        )
        try:
            container.stop(timeout=5)
        except docker.errors.APIError:
            pass  # already stopped/stopping - `remove` below still applies
        container.remove(force=True)
        self._last_used.pop(session_id, None)

    def _adopt(self, session_id: str, container: Any) -> datetime:
        """Seed `_last_used[session_id]` from the container's own
        `State.StartedAt` (an ISO8601 string Docker always sets) the first
        time this process sees a container it doesn't recognize, and return
        the seeded value.

        Falls back to `now` (i.e. treat it as freshly-used) if `StartedAt`
        is ever missing or unparseable - the same "worst case: looks
        freshly-used" tradeoff this class's own docstring already accepts
        for `_last_used` not surviving a restart at all.
        """
        started_at_raw = container.attrs.get("State", {}).get("StartedAt")
        try:
            started_at = datetime.fromisoformat(started_at_raw)
        except (TypeError, ValueError):
            started_at = datetime.now(UTC)
        self._last_used[session_id] = started_at
        return started_at


def _truncate_bytes(data: bytes | None) -> tuple[bytes, bool]:
    data = data or b""
    if len(data) <= MAX_OUTPUT_BYTES:
        return data, False
    return data[:MAX_OUTPUT_BYTES], True

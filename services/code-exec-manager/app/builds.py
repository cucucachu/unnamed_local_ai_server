"""App builds (M12-04): one short-lived builder container per build phase.

The platform stages an app's source under `{app_builds_host_dir}/{build_id}/`
and calls `POST /builds/{build_id}/{phase}` (`app/api.py`), authenticated
with its build token. The caller supplies only the id and the phase;
the image, the mounts and every hardening flag are fixed here, the same way
`app.sessions.build_run_kwargs` fixes an exec container. Nothing the build
runs can reach the network, docker.sock, or anything on the host outside
its own build dir.

`compile` runs no app code (esbuild and tsc only read it), so it's the only
phase that can write the bundle. `smoke` renders the app, which runs its
code in jsdom - not a sandbox - so it sees the bundle read-only and writes
only its own result.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any

import anyio.to_thread
import docker.errors
import requests.exceptions
from docker.types import Mount
from fastapi import HTTPException

from app.core.config import Settings

logger = logging.getLogger(__name__)

BUILD_ID_PATTERN = r"^[a-f0-9]{32}$"
BUILD_PHASE_PATTERN = r"^(compile|smoke)$"

BUILDER_USER = "19999:19999"
"""Matches the builder image's `USER`; the platform makes a build's output
dirs writable by this uid and nothing else."""

MAX_LOG_BYTES = 64_000
"""Per stream. The build's real output is the `result.json` it writes; logs
are only for a crash's stack trace."""

WAIT_GRACE_S = 15
"""Added to `build_timeout_s` for the outer guard around the Docker API
calls themselves, as in `app.sessions.OUTER_TIMEOUT_GRACE_S`."""


def build_container_name(build_id: str, phase: str) -> str:
    return f"homeai-build-{build_id}-{phase}"


def build_mounts(build_id: str, phase: str, settings: Settings) -> list[Mount]:
    root = PurePosixPath(settings.app_builds_host_dir) / build_id
    src = Mount("/src", str(root / "src"), type="bind", read_only=True)
    if phase == "compile":
        return [src, Mount("/out", str(root / "bundle"), type="bind", read_only=False)]
    return [
        src,
        Mount("/bundle", str(root / "bundle"), type="bind", read_only=True),
        Mount("/out", str(root / "smoke"), type="bind", read_only=False),
    ]


def build_run_kwargs(build_id: str, phase: str, settings: Settings) -> dict[str, Any]:
    """The builder container spec, as a plain dict for `containers.run(**kwargs)`.

    Pure, like `app.sessions.build_run_kwargs`, so the hardening test can
    assert it field by field. `--mount` binds (not `-v`) so a build dir that
    was never staged fails the create instead of being made on the host.
    """
    return {
        "image": settings.builder_image,
        "name": build_container_name(build_id, phase),
        "command": ["node", "/builder/src/cli.mjs", phase],
        "detach": True,
        "network_mode": "none",
        "cap_drop": ["ALL"],
        "security_opt": ["no-new-privileges"],
        "read_only": True,
        "tmpfs": {"/tmp": "size=256m"},
        "mem_limit": "2g",
        "nano_cpus": 2_000_000_000,
        "user": BUILDER_USER,
        "pids_limit": 256,
        "mounts": build_mounts(build_id, phase, settings),
        "labels": {"homeai.build": build_id, "homeai.build.phase": phase},
    }


@dataclass(frozen=True)
class BuildPhaseResult:
    exit_code: int
    timed_out: bool
    duration_ms: int
    stdout: str
    stderr: str
    truncated: bool


class BuildRunner:
    """Runs build phases, at most `build_concurrency` at a time."""

    def __init__(self, docker_client: Any, settings: Settings) -> None:
        self._client = docker_client
        self._settings = settings
        self._slots = asyncio.Semaphore(max(1, settings.build_concurrency))

    async def run(self, build_id: str, phase: str) -> BuildPhaseResult:
        timeout_s = self._settings.build_timeout_s
        async with self._slots:
            start = time.monotonic()
            container = await anyio.to_thread.run_sync(self._create, build_id, phase)
            try:
                exit_code, timed_out = await asyncio.wait_for(
                    anyio.to_thread.run_sync(self._wait, container, timeout_s),
                    timeout=timeout_s + WAIT_GRACE_S,
                )
            except TimeoutError:
                exit_code, timed_out = -1, True
            try:
                stdout, stderr, truncated = await anyio.to_thread.run_sync(self._logs, container)
            finally:
                await anyio.to_thread.run_sync(_force_remove, container)
            return BuildPhaseResult(
                exit_code=exit_code,
                timed_out=timed_out,
                duration_ms=int((time.monotonic() - start) * 1000),
                stdout=stdout,
                stderr=stderr,
                truncated=truncated,
            )

    def _create(self, build_id: str, phase: str) -> Any:
        try:
            return self._client.containers.run(**build_run_kwargs(build_id, phase, self._settings))
        except docker.errors.ImageNotFound as exc:
            raise HTTPException(503, "builder image not available") from exc
        except docker.errors.APIError as exc:
            if exc.status_code == 409:
                raise HTTPException(409, "this build phase is already running") from exc
            if "does not exist" in str(exc):
                raise HTTPException(404, "build not staged") from exc
            raise

    def _wait(self, container: Any, timeout_s: int) -> tuple[int, bool]:
        try:
            status = container.wait(timeout=timeout_s)
        except requests.exceptions.RequestException:
            _kill(container)
            return -1, True
        return int(status.get("StatusCode", -1)), False

    def _logs(self, container: Any) -> tuple[str, str, bool]:
        streams = []
        truncated = False
        for stdout in (True, False):
            data = container.logs(stdout=stdout, stderr=not stdout) or b""
            if len(data) > MAX_LOG_BYTES:
                data, truncated = data[-MAX_LOG_BYTES:], True
            streams.append(data.decode("utf-8", errors="replace"))
        return streams[0], streams[1], truncated

    async def remove_stale(self) -> None:
        """Remove build containers a previous process left behind; never raises."""
        await anyio.to_thread.run_sync(self._remove_stale_sync)

    def _remove_stale_sync(self) -> None:
        try:
            containers = self._client.containers.list(all=True, filters={"label": "homeai.build"})
        except Exception:
            logger.exception("builds: failed to list stale build containers")
            return
        for container in containers:
            logger.info("builds: removing stale build container %r", container.name)
            _force_remove(container)


def _kill(container: Any) -> None:
    try:
        container.kill()
    except docker.errors.APIError:
        pass


def _force_remove(container: Any) -> None:
    try:
        container.remove(force=True)
    except docker.errors.APIError:
        pass

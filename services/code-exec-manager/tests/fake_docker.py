"""A stub Docker SDK client for unit tests.

Implements only the subset of `docker.DockerClient`'s surface that
`app.sessions.SessionManager` and `app.builds.BuildRunner` actually call
(`containers.get/run/list`, and per-container
`.reload/.exec_run/.wait/.logs/.stop/.remove/.kill`), so unit tests never
need a real Docker daemon. `tests/test_sessions_integration.py`
exercises the real `docker` SDK against a real daemon instead.
"""

from __future__ import annotations

from typing import Any

import docker.errors


class FakeExecResult:
    """Mirrors the real SDK's `container.exec_run(...)` return shape closely
    enough for `SessionManager._exec_sync` to consume it unmodified."""

    def __init__(self, exit_code: int, stdout: bytes = b"", stderr: bytes = b"") -> None:
        self.exit_code = exit_code
        self.output = (stdout, stderr)


class FakeContainer:
    # Arbitrary fixed default so tests that don't care about adoption/
    # `StartedAt` never need to set it explicitly - matches the real SDK's
    # `container.attrs["State"]["StartedAt"]` shape (ISO8601, `Z`-suffixed,
    # nanosecond-precision) closely enough for `SessionManager._adopt`'s
    # `datetime.fromisoformat` to parse it unmodified.
    DEFAULT_STARTED_AT = "2024-01-01T00:00:00.000000000Z"

    def __init__(self, name: str, image: str, **kwargs: Any) -> None:
        self.name = name
        self.image = image
        self.labels: dict[str, str] = kwargs.get("labels") or {}
        self.run_kwargs = kwargs
        self.id = f"fake-{name}"
        self.status = "running"
        self.stopped = False
        self.removed = False
        self.killed = False
        self.last_exec_cmd: list[str] | None = None
        self.last_exec_user: str | None = None
        # Test hook: either a fixed `FakeExecResult`, or a callable
        # `(cmd, demux, user) -> FakeExecResult` for tests simulating a
        # slow/hanging exec (e.g. via `time.sleep`).
        self.exec_run_result: FakeExecResult | Any = FakeExecResult(0)
        # Mirrors the real SDK's `.attrs` dict closely enough for the
        # reaper's adoption path (`container.attrs["State"]["StartedAt"]`)
        # to work unmodified - tests simulating an "unknown" container set
        # `container.attrs["State"]["StartedAt"]` directly, the same way
        # existing tests mutate `container.status` directly.
        self.attrs: dict[str, Any] = {"State": {"StartedAt": self.DEFAULT_STARTED_AT}}
        # Test hooks for build containers (`app.builds`): what `wait` returns
        # (or a callable `(timeout) -> dict`, which may raise), and `logs`.
        self.wait_result: dict[str, Any] | Any = {"StatusCode": 0}
        self.log_output: dict[str, bytes] = {"stdout": b"", "stderr": b""}

    def reload(self) -> None:
        pass  # status is mutated directly by fakes/tests; nothing to refresh

    def exec_run(
        self, cmd: list[str], demux: bool = False, user: str | None = None
    ) -> FakeExecResult:
        self.last_exec_cmd = cmd
        self.last_exec_user = user
        if callable(self.exec_run_result):
            return self.exec_run_result(cmd, demux, user)
        return self.exec_run_result

    def wait(self, timeout: int | None = None) -> dict[str, Any]:
        if callable(self.wait_result):
            return self.wait_result(timeout)
        return self.wait_result

    def logs(self, stdout: bool = True, stderr: bool = True) -> bytes:
        return (self.log_output["stdout"] if stdout else b"") + (
            self.log_output["stderr"] if stderr else b""
        )

    def stop(self, timeout: int = 10) -> None:
        self.stopped = True
        self.status = "exited"

    def remove(self, force: bool = False) -> None:
        self.removed = True

    def kill(self) -> None:
        self.killed = True


class FakeContainerCollection:
    def __init__(self) -> None:
        self._by_name: dict[str, FakeContainer] = {}
        self.run_calls: list[dict[str, Any]] = []
        # Test hook for `tests/test_reaper_unit.py`'s "listing error doesn't
        # crash the reap loop" case - when set, `list()` raises this instead
        # of returning, simulating a transient Docker Engine API failure.
        self.list_error: Exception | None = None
        # When set, `run()` raises this instead (a missing bind source, a
        # name conflict, a missing image).
        self.run_error: Exception | None = None
        # Called with each container `run()` creates, before it's returned.
        self.on_run: Any = None

    def get(self, name: str) -> FakeContainer:
        container = self._by_name.get(name)
        if container is None or container.removed:
            raise docker.errors.NotFound(f"no such container: {name}")
        return container

    def run(self, image: str, name: str, **kwargs: Any) -> FakeContainer:
        self.run_calls.append({"image": image, "name": name, **kwargs})
        if self.run_error is not None:
            raise self.run_error
        container = FakeContainer(name=name, image=image, **kwargs)
        self._by_name[name] = container
        if self.on_run is not None:
            self.on_run(container)
        return container

    def list(self, all: bool = False, filters: dict[str, Any] | None = None) -> list[FakeContainer]:
        if self.list_error is not None:
            raise self.list_error
        containers = [c for c in self._by_name.values() if not c.removed]
        if not all:
            containers = [c for c in containers if c.status == "running"]
        if filters and "label" in filters:
            key, eq, value = filters["label"].partition("=")
            containers = [
                c for c in containers if key in c.labels and (not eq or c.labels[key] == value)
            ]
        return containers


class FakeDockerClient:
    def __init__(self) -> None:
        self.containers = FakeContainerCollection()

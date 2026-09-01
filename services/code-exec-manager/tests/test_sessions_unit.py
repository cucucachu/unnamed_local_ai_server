"""Unit tests for `app.sessions.SessionManager` against `FakeDockerClient` -
no real Docker daemon required. `tests/test_sessions_integration.py` (marked
`integration`) re-runs the same lifecycle against the real SDK + a real
toolbox image.
"""

from __future__ import annotations

import time

import pytest
from fastapi import HTTPException

from app.core.config import Settings
from app.sessions import GNU_TIMEOUT_EXIT_CODE, MAX_OUTPUT_BYTES, SessionManager, container_name
from tests.fake_docker import FakeDockerClient, FakeExecResult


async def test_ensure_creates_fresh_container_when_absent(
    manager: SessionManager, fake_docker: FakeDockerClient
) -> None:
    result = await manager.ensure("sess-1")

    assert result["created"] is True
    assert result["container_id"] == fake_docker.containers.get(container_name("sess-1")).id
    assert len(fake_docker.containers.run_calls) == 1


async def test_ensure_is_a_noop_when_already_running(
    manager: SessionManager, fake_docker: FakeDockerClient
) -> None:
    first = await manager.ensure("sess-1")
    second = await manager.ensure("sess-1")

    assert first["created"] is True
    assert second["created"] is False
    assert first["container_id"] == second["container_id"]
    assert len(fake_docker.containers.run_calls) == 1


async def test_ensure_recreates_when_existing_container_is_stopped(
    manager: SessionManager, fake_docker: FakeDockerClient
) -> None:
    await manager.ensure("sess-1")
    stopped_container = fake_docker.containers.get(container_name("sess-1"))
    stopped_container.status = "exited"

    second = await manager.ensure("sess-1")

    assert second["created"] is True
    assert stopped_container.removed is True
    assert len(fake_docker.containers.run_calls) == 2
    # The name-collision cleanup from the old container mustn't leak forward.
    fresh = fake_docker.containers.get(container_name("sess-1"))
    assert fresh.status == "running"
    assert fresh.removed is False


async def test_ensure_updates_last_used(manager: SessionManager) -> None:
    # White-box on purpose - `last_used` is intentionally not part of the
    # public API (only exposed indirectly via `list_sessions`).
    await manager.ensure("sess-1")
    assert "sess-1" in manager._last_used


async def test_execute_happy_path_returns_stdout_and_exit_code(
    manager: SessionManager, fake_docker: FakeDockerClient
) -> None:
    await manager.ensure("sess-1")
    container = fake_docker.containers.get(container_name("sess-1"))
    container.exec_run_result = FakeExecResult(0, stdout=b"42\n", stderr=b"")

    result = await manager.execute("sess-1", "python3 -c 'print(6*7)'", timeout_seconds=5)

    assert result.stdout == "42\n"
    assert result.stderr == ""
    assert result.exit_code == 0
    assert result.timed_out is False
    assert result.truncated is False
    assert result.duration_ms >= 0


async def test_execute_wraps_command_with_gnu_timeout_and_explicit_user(
    manager: SessionManager, fake_docker: FakeDockerClient, test_settings: Settings
) -> None:
    await manager.ensure("sess-1")
    container = fake_docker.containers.get(container_name("sess-1"))

    await manager.execute("sess-1", "echo hi", timeout_seconds=7)

    assert container.last_exec_cmd == [
        "timeout",
        "--signal=TERM",
        "--kill-after=5",
        "7s",
        "bash",
        "-lc",
        "echo hi",
    ]
    assert container.last_exec_user == f"{test_settings.homeai_uid}:{test_settings.homeai_gid}"


async def test_execute_on_nonexistent_session_raises_404(manager: SessionManager) -> None:
    with pytest.raises(HTTPException) as exc_info:
        await manager.execute("never-ensured", "echo hi", timeout_seconds=5)
    assert exc_info.value.status_code == 404


async def test_execute_maps_gnu_timeout_exit_code_to_timed_out(
    manager: SessionManager, fake_docker: FakeDockerClient
) -> None:
    await manager.ensure("sess-1")
    container = fake_docker.containers.get(container_name("sess-1"))
    container.exec_run_result = FakeExecResult(GNU_TIMEOUT_EXIT_CODE, stdout=b"partial", stderr=b"")

    result = await manager.execute("sess-1", "sleep 30", timeout_seconds=2)

    assert result.timed_out is True
    assert result.exit_code == GNU_TIMEOUT_EXIT_CODE  # kept, not replaced, per §7


async def test_execute_truncates_oversized_stdout_and_stderr(
    manager: SessionManager, fake_docker: FakeDockerClient
) -> None:
    await manager.ensure("sess-1")
    container = fake_docker.containers.get(container_name("sess-1"))
    oversized = b"x" * (MAX_OUTPUT_BYTES + 10)
    container.exec_run_result = FakeExecResult(0, stdout=oversized, stderr=b"y" * (MAX_OUTPUT_BYTES + 1))

    result = await manager.execute("sess-1", "yes x", timeout_seconds=5)

    assert len(result.stdout.encode("utf-8")) == MAX_OUTPUT_BYTES
    assert len(result.stderr.encode("utf-8")) == MAX_OUTPUT_BYTES
    assert result.truncated is True


async def test_execute_decodes_invalid_utf8_with_replace(
    manager: SessionManager, fake_docker: FakeDockerClient
) -> None:
    await manager.ensure("sess-1")
    container = fake_docker.containers.get(container_name("sess-1"))
    container.exec_run_result = FakeExecResult(0, stdout=b"\xff\xfe bad bytes", stderr=b"")

    result = await manager.execute("sess-1", "cmd", timeout_seconds=5)

    assert "\ufffd" in result.stdout  # U+FFFD replacement character


async def test_execute_outer_timeout_kills_and_removes_container(
    manager: SessionManager, fake_docker: FakeDockerClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    import app.sessions as sessions_module

    # Shrink the outer grace window so the test doesn't actually wait 15s+ -
    # the hung-docker-API scenario this guards against, not GNU `timeout`
    # itself (which the fake below never invokes - it just blocks).
    monkeypatch.setattr(sessions_module, "OUTER_TIMEOUT_GRACE_S", 0.05)

    await manager.ensure("sess-1")
    container = fake_docker.containers.get(container_name("sess-1"))

    def hang(cmd, demux, user):
        time.sleep(2)  # longer than timeout_seconds + the shrunk grace window
        return FakeExecResult(0, stdout=b"too late")

    container.exec_run_result = hang

    result = await manager.execute("sess-1", "cmd", timeout_seconds=0)

    assert result.timed_out is True
    assert result.exit_code == -1
    assert result.stdout == ""
    assert result.stderr == ""
    assert container.killed is True
    assert container.removed is True


async def test_remove_stops_and_removes_existing_container(
    manager: SessionManager, fake_docker: FakeDockerClient
) -> None:
    await manager.ensure("sess-1")
    container = fake_docker.containers.get(container_name("sess-1"))

    await manager.remove("sess-1")

    assert container.stopped is True
    assert container.removed is True


async def test_remove_is_idempotent_for_nonexistent_session(manager: SessionManager) -> None:
    await manager.remove("never-existed")  # must not raise


async def test_remove_clears_last_used(manager: SessionManager) -> None:
    await manager.ensure("sess-1")
    await manager.remove("sess-1")
    assert "sess-1" not in manager._last_used


async def test_list_sessions_only_returns_labeled_containers(manager: SessionManager) -> None:
    await manager.ensure("sess-1")
    await manager.ensure("sess-2")

    sessions = await manager.list_sessions()

    assert {s.session_id for s in sessions} == {"sess-1", "sess-2"}
    assert all(s.last_used is not None for s in sessions)


async def test_list_sessions_excludes_removed_containers(manager: SessionManager) -> None:
    await manager.ensure("sess-1")
    await manager.ensure("sess-2")
    await manager.remove("sess-1")

    sessions = await manager.list_sessions()

    assert {s.session_id for s in sessions} == {"sess-2"}

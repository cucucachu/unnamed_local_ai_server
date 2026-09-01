"""Integration tests for `app.sessions.SessionManager` against a REAL Docker
daemon (`/var/run/docker.sock`) and the REAL `homeai-exec-toolbox:latest`
image (M4-01) - the two things `tests/fake_docker.py` deliberately can't
exercise (real hardening flags actually being accepted/enforced by
`dockerd`, a real bind mount, a real GNU `timeout` wrapping a real `sleep`).

Skipped (not failed) when either is unavailable, so a plain `uv run pytest`
never requires Docker - same "skip, don't fail, on a missing real resource"
policy as agent-server's `tests/test_checkpointer_pg.py`.

Run for real:

    uv run pytest -m integration
"""

from __future__ import annotations

import time

import docker
import docker.errors
import pytest

from app.core.config import Settings
from app.sessions import SessionManager, container_name

TOOLBOX_IMAGE = "homeai-exec-toolbox:latest"
SESSION_ID = "pytest-integration"


def _probe() -> tuple[docker.DockerClient | None, str]:
    try:
        client = docker.from_env()
        client.ping()
    except Exception as exc:  # noqa: BLE001 - any failure just means "skip"
        return None, f"Docker daemon unreachable: {exc}"
    try:
        client.images.get(TOOLBOX_IMAGE)
    except docker.errors.ImageNotFound:
        return None, (
            f"{TOOLBOX_IMAGE} not built - run "
            "services/code-exec-manager/build-exec-image.sh (M4-01) first"
        )
    return client, ""


_real_client, _skip_reason = _probe()

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(_real_client is None, reason=_skip_reason),
]


@pytest.fixture
def real_docker_client() -> docker.DockerClient:
    return _real_client


@pytest.fixture
def real_settings(tmp_path) -> Settings:
    return Settings(workspace_host_dir=str(tmp_path), toolbox_image=TOOLBOX_IMAGE, _env_file=None)


@pytest.fixture
def real_manager(real_docker_client: docker.DockerClient, real_settings: Settings) -> SessionManager:
    return SessionManager(real_docker_client, real_settings)


@pytest.fixture(autouse=True)
def _cleanup(real_docker_client: docker.DockerClient):
    yield
    try:
        real_docker_client.containers.get(container_name(SESSION_ID)).remove(force=True)
    except docker.errors.NotFound:
        pass


async def test_ensure_then_ensure_again_reuses_container(real_manager: SessionManager) -> None:
    first = await real_manager.ensure(SESSION_ID)
    second = await real_manager.ensure(SESSION_ID)

    assert first["created"] is True
    assert second["created"] is False
    assert first["container_id"] == second["container_id"]


async def test_execute_echo_hi(real_manager: SessionManager) -> None:
    await real_manager.ensure(SESSION_ID)

    result = await real_manager.execute(SESSION_ID, "echo hi", timeout_seconds=10)

    assert result.stdout == "hi\n"
    assert result.exit_code == 0
    assert result.timed_out is False


async def test_execute_sleep_beyond_timeout_reports_timed_out_quickly(
    real_manager: SessionManager,
) -> None:
    await real_manager.ensure(SESSION_ID)

    start = time.monotonic()
    result = await real_manager.execute(SESSION_ID, "sleep 30", timeout_seconds=2)
    elapsed = time.monotonic() - start

    assert result.timed_out is True
    assert elapsed < 10


async def test_file_written_in_workspace_visible_at_host_path(
    real_manager: SessionManager, tmp_path
) -> None:
    await real_manager.ensure(SESSION_ID)

    result = await real_manager.execute(
        SESSION_ID, "echo hello > /workspace/from-container.txt", timeout_seconds=10
    )

    assert result.exit_code == 0
    assert (tmp_path / "from-container.txt").read_text() == "hello\n"


async def test_delete_removes_container_entirely(
    real_manager: SessionManager, real_docker_client: docker.DockerClient
) -> None:
    await real_manager.ensure(SESSION_ID)

    await real_manager.remove(SESSION_ID)

    with pytest.raises(docker.errors.NotFound):
        real_docker_client.containers.get(container_name(SESSION_ID))

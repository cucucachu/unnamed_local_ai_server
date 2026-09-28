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

import sqlite3
import stat
import time

import docker
import docker.errors
import pytest

from app.core.config import Settings
from app.grants import Grants, Mount
from app.sessions import SessionManager, container_name
from tests.conftest import USER_A

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
def real_settings() -> Settings:
    return Settings(toolbox_image=TOOLBOX_IMAGE, _env_file=None)


@pytest.fixture
def grants(tmp_path) -> Grants:
    """A personal dir and a viewer-only shared dir. The test can't chown to a
    space gid, so both are world-writable setgid dirs: only the mount mode
    stands between the viewer and a write."""
    dirs = {}
    for name in ("personal", "family"):
        path = tmp_path / name
        path.mkdir()
        path.chmod(0o2777)
        dirs[name] = str(path)
    return Grants(
        USER_A,
        20001,
        30001,
        (30001, 30050),
        (
            Mount(dirs["personal"], "/files/personal", False),
            Mount(dirs["family"], "/files/spaces/family", True),
        ),
    )


@pytest.fixture
def real_manager(
    real_docker_client: docker.DockerClient, real_settings: Settings
) -> SessionManager:
    return SessionManager(real_docker_client, real_settings)


@pytest.fixture(autouse=True)
def _cleanup(real_docker_client: docker.DockerClient):
    yield
    try:
        real_docker_client.containers.get(container_name(SESSION_ID)).remove(force=True)
    except docker.errors.NotFound:
        pass


async def test_ensure_then_ensure_again_reuses_container(
    real_manager: SessionManager, grants: Grants
) -> None:
    first = await real_manager.ensure(SESSION_ID, grants)
    second = await real_manager.ensure(SESSION_ID, grants)

    assert first["created"] is True
    assert second["created"] is False
    assert first["container_id"] == second["container_id"]


async def test_execute_echo_hi(real_manager: SessionManager, grants: Grants) -> None:
    await real_manager.ensure(SESSION_ID, grants)

    result = await real_manager.execute(SESSION_ID, grants, "echo hi", timeout_seconds=10)

    assert result.stdout == "hi\n"
    assert result.exit_code == 0
    assert result.timed_out is False


async def test_execute_sleep_beyond_timeout_reports_timed_out_quickly(
    real_manager: SessionManager, grants: Grants
) -> None:
    await real_manager.ensure(SESSION_ID, grants)

    start = time.monotonic()
    result = await real_manager.execute(SESSION_ID, grants, "sleep 30", timeout_seconds=2)
    elapsed = time.monotonic() - start

    assert result.timed_out is True
    assert elapsed < 10


async def test_runs_as_the_grants_uid_with_every_space_gid(
    real_manager: SessionManager, grants: Grants
) -> None:
    await real_manager.ensure(SESSION_ID, grants)

    result = await real_manager.execute(
        SESSION_ID, grants, "id -u; id -g; id -G", timeout_seconds=10
    )

    assert result.stdout.split("\n")[:3] == ["20001", "30001", "30001 30050"]


async def test_file_written_in_container_visible_at_host_path_group_writable(
    real_manager: SessionManager, grants: Grants, tmp_path
) -> None:
    await real_manager.ensure(SESSION_ID, grants)

    result = await real_manager.execute(
        SESSION_ID, grants, "echo hello > /files/personal/from-container.txt", timeout_seconds=10
    )

    assert result.exit_code == 0, result.stderr
    written = tmp_path / "personal" / "from-container.txt"
    assert written.read_text() == "hello\n"
    st = written.stat()
    assert st.st_uid == 20001
    assert stat.S_IMODE(st.st_mode) == 0o664


async def test_viewer_mount_is_read_only_and_nothing_else_is_mounted(
    real_manager: SessionManager, grants: Grants, tmp_path
) -> None:
    await real_manager.ensure(SESSION_ID, grants)

    write = await real_manager.execute(
        SESSION_ID, grants, "touch /files/spaces/family/x", timeout_seconds=10
    )
    listing = await real_manager.execute(
        SESSION_ID, grants, "ls /files /files/spaces", timeout_seconds=10
    )

    assert write.exit_code != 0
    assert "Read-only file system" in write.stderr
    assert not (tmp_path / "family" / "x").exists()
    assert listing.stdout.split() == ["/files:", "personal", "spaces", "/files/spaces:", "family"]


async def test_changed_grants_recreate_the_container(
    real_manager: SessionManager, grants: Grants
) -> None:
    first = await real_manager.ensure(SESSION_ID, grants)
    fewer = Grants(grants.user_id, grants.uid, grants.gid, grants.gids[:1], grants.mounts[:1])

    second = await real_manager.ensure(SESSION_ID, fewer)
    listing = await real_manager.execute(SESSION_ID, fewer, "ls /files/spaces", timeout_seconds=10)

    assert second["created"] is True
    assert second["container_id"] != first["container_id"]
    assert listing.exit_code != 0


async def test_delete_removes_container_entirely(
    real_manager: SessionManager, grants: Grants, real_docker_client: docker.DockerClient
) -> None:
    await real_manager.ensure(SESSION_ID, grants)

    await real_manager.remove(SESSION_ID, USER_A)

    with pytest.raises(docker.errors.NotFound):
        real_docker_client.containers.get(container_name(SESSION_ID))


async def test_app_data_is_readable_with_sqlite_but_not_writable(
    real_manager: SessionManager, grants: Grants, tmp_path
) -> None:
    ro = tmp_path / "ro"
    ro.mkdir()
    db = sqlite3.connect(ro / "data.sqlite")
    db.execute("CREATE TABLE items (name TEXT)")
    db.execute("INSERT INTO items VALUES ('Milk')")
    db.commit()
    db.close()
    ro.chmod(0o755)
    (ro / "data.sqlite").chmod(0o644)
    target = "/app-data/spaces/family/groceries"
    with_app = Grants(
        grants.user_id, grants.uid, grants.gid, grants.gids,
        (*grants.mounts, Mount(str(ro), target, True)),
    )  # fmt: skip
    await real_manager.ensure(SESSION_ID, with_app)

    read = await real_manager.execute(
        SESSION_ID,
        with_app,
        'python3 -c "import sqlite3; c = sqlite3.connect('
        f"'file:{target}/data.sqlite?mode=ro&immutable=1', uri=True); "
        "print(c.execute('SELECT name FROM items').fetchall())\"",
        timeout_seconds=20,
    )
    write = await real_manager.execute(
        SESSION_ID, with_app, f"touch {target}/x", timeout_seconds=10
    )

    assert read.stdout.strip() == "[('Milk',)]", read.stderr
    assert write.exit_code != 0
    assert "Read-only file system" in write.stderr

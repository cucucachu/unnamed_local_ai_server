"""`POST /builds/{build_id}/{phase}` (`app/builds.py`): the builder
container spec field by field, the build-token check, and the run /
wait / timeout / cleanup lifecycle against the fake Docker client."""

from __future__ import annotations

from typing import Any

import docker.errors
import pytest
import requests.exceptions
from httpx import AsyncClient

from app.builds import BuildRunner, build_run_kwargs
from app.core.config import Settings
from tests.conftest import BUILD_TOKEN, SERVICE_TOKEN, Issuer
from tests.fake_docker import FakeContainer, FakeDockerClient

BUILD_ID = "0123456789abcdef0123456789abcdef"
AUTH = {"Authorization": f"Bearer {BUILD_TOKEN}"}


def _bind(source: str, target: str, read_only: bool) -> dict[str, Any]:
    return {"Target": target, "Source": source, "Type": "bind", "ReadOnly": read_only}


def test_compile_spec_matches_reference_exactly() -> None:
    settings = Settings(app_builds_host_dir="/srv/homeai/builds", _env_file=None)
    root = f"/srv/homeai/builds/{BUILD_ID}"

    assert build_run_kwargs(BUILD_ID, "compile", settings) == {
        "image": "homeai-app-builder:latest",
        "name": f"homeai-build-{BUILD_ID}-compile",
        "command": ["node", "/builder/src/cli.mjs", "compile"],
        "detach": True,
        "network_mode": "none",
        "cap_drop": ["ALL"],
        "security_opt": ["no-new-privileges"],
        "read_only": True,
        "tmpfs": {"/tmp": "size=256m"},
        "mem_limit": "2g",
        "nano_cpus": 2_000_000_000,
        "user": "19999:19999",
        "pids_limit": 256,
        "mounts": [
            _bind(f"{root}/src", "/src", True),
            _bind(f"{root}/bundle", "/out", False),
        ],
        "labels": {"homeai.build": BUILD_ID, "homeai.build.phase": "compile"},
    }


def test_smoke_sees_the_bundle_read_only_and_writes_only_its_own_dir() -> None:
    settings = Settings(app_builds_host_dir="/srv/homeai/builds", _env_file=None)
    root = f"/srv/homeai/builds/{BUILD_ID}"

    spec = build_run_kwargs(BUILD_ID, "smoke", settings)

    assert spec["command"] == ["node", "/builder/src/cli.mjs", "smoke"]
    assert spec["mounts"] == [
        _bind(f"{root}/src", "/src", True),
        _bind(f"{root}/bundle", "/bundle", True),
        _bind(f"{root}/smoke", "/out", False),
    ]
    assert [m["Target"] for m in spec["mounts"] if not m["ReadOnly"]] == ["/out"]
    assert "docker.sock" not in repr(spec)
    assert "privileged" not in spec and "volumes" not in spec and "environment" not in spec


async def test_a_phase_runs_waits_and_removes_its_container(
    client: AsyncClient, fake_docker: FakeDockerClient
) -> None:
    def on_run(container: FakeContainer) -> None:
        container.wait_result = {"StatusCode": 0}
        container.log_output = {"stdout": b"", "stderr": b"warn\n"}

    fake_docker.containers.on_run = on_run

    response = await client.post(f"/builds/{BUILD_ID}/compile", headers=AUTH)

    assert response.status_code == 200
    body = response.json()
    assert body | {"duration_ms": 0} == {
        "exit_code": 0,
        "timed_out": False,
        "duration_ms": 0,
        "stdout": "",
        "stderr": "warn\n",
        "truncated": False,
    }
    [call] = fake_docker.containers.run_calls
    assert call["name"] == f"homeai-build-{BUILD_ID}-compile"
    assert fake_docker.containers.list(all=True) == []


async def test_a_phase_that_overruns_is_killed_and_reported(
    client: AsyncClient, fake_docker: FakeDockerClient
) -> None:
    created: list[FakeContainer] = []

    def hang(timeout: int | None) -> dict[str, Any]:
        raise requests.exceptions.ReadTimeout("timed out")

    def on_run(container: FakeContainer) -> None:
        container.wait_result = hang
        container.log_output = {"stdout": b"", "stderr": b"x" * 100_000}
        created.append(container)

    fake_docker.containers.on_run = on_run

    response = await client.post(f"/builds/{BUILD_ID}/smoke", headers=AUTH)

    assert response.status_code == 200
    body = response.json()
    assert body["timed_out"] is True and body["exit_code"] == -1
    assert body["truncated"] is True and len(body["stderr"]) == 64_000
    assert created[0].killed and created[0].removed


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"Authorization": "Bearer wrong"},
        {"Authorization": BUILD_TOKEN},
        {"Authorization": f"Basic {BUILD_TOKEN}"},
        {"Authorization": f"Bearer {SERVICE_TOKEN}"},
    ],
)
async def test_the_build_token_is_required(
    client: AsyncClient, fake_docker: FakeDockerClient, headers: dict[str, str]
) -> None:
    response = await client.post(f"/builds/{BUILD_ID}/compile", headers=headers)

    assert response.status_code == 401
    assert fake_docker.containers.run_calls == []


async def test_a_user_delegation_is_not_the_build_token(
    client: AsyncClient, fake_docker: FakeDockerClient, issuer: Issuer
) -> None:
    response = await client.post(f"/builds/{BUILD_ID}/compile", headers=issuer.headers(BUILD_ID))

    assert response.status_code == 401
    assert fake_docker.containers.run_calls == []


@pytest.mark.parametrize(
    "path",
    [
        f"/builds/{BUILD_ID.upper()}/compile",
        f"/builds/{BUILD_ID[:-1]}/compile",
        "/builds/..%2F..%2Fetc/compile",
        f"/builds/{BUILD_ID}/shell",
        f"/builds/{BUILD_ID}/compile%20",
    ],
)
async def test_only_a_build_id_and_a_known_phase_are_accepted(
    client: AsyncClient, fake_docker: FakeDockerClient, path: str
) -> None:
    response = await client.post(path, headers=AUTH)

    assert response.status_code in (404, 422)
    assert fake_docker.containers.run_calls == []


@pytest.mark.parametrize("overrides", [{"platform_build_token": ""}, {"app_builds_host_dir": ""}])
async def test_builds_fail_closed_until_configured(
    fake_docker: FakeDockerClient, overrides: dict[str, str]
) -> None:
    from httpx import ASGITransport

    from app.main import create_app

    settings = Settings(
        platform_exec_token=SERVICE_TOKEN,
        platform_build_token=BUILD_TOKEN,
        app_builds_host_dir="/srv/homeai/builds",
        _env_file=None,
    ).model_copy(update=overrides)
    application = create_app(settings, docker_client_override=fake_docker)
    async with application.router.lifespan_context(application):
        transport = ASGITransport(app=application)
        async with AsyncClient(transport=transport, base_url="http://t") as c:
            response = await c.post(
                f"/builds/{BUILD_ID}/compile", headers={"Authorization": "Bearer "}
            )

    assert response.status_code == 503
    assert fake_docker.containers.run_calls == []


@pytest.mark.parametrize(
    ("error", "status"),
    [
        (docker.errors.ImageNotFound("no such image"), 503),
        (
            docker.errors.APIError(
                "invalid mount config: bind source path does not exist: /srv/homeai/builds/x/src"
            ),
            404,
        ),
    ],
)
async def test_create_errors_map_to_statuses(
    client: AsyncClient, fake_docker: FakeDockerClient, error: Exception, status: int
) -> None:
    fake_docker.containers.run_error = error

    response = await client.post(f"/builds/{BUILD_ID}/compile", headers=AUTH)

    assert response.status_code == status


async def test_stale_build_containers_are_removed_at_startup(
    fake_docker: FakeDockerClient, test_settings: Settings
) -> None:
    stale = fake_docker.containers.run(
        "homeai-app-builder:latest", "homeai-build-old-smoke", labels={"homeai.build": "old"}
    )
    session = fake_docker.containers.run(
        "homeai-exec-toolbox:latest", "homeai-exec-t", labels={"homeai.exec": "1"}
    )

    await BuildRunner(fake_docker, test_settings).remove_stale()

    assert stale.removed and not session.removed

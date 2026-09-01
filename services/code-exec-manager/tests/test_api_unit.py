"""HTTP-level unit tests for the four §7 endpoints (`app/api.py`), against
`FakeDockerClient` via `create_app(docker_client_override=...)` - no real
Docker daemon required.
"""

from __future__ import annotations

from httpx import AsyncClient

from app.sessions import container_name
from tests.fake_docker import FakeDockerClient, FakeExecResult


async def test_ensure_returns_201_like_200_with_created_true_first_time(client: AsyncClient) -> None:
    response = await client.post("/sessions/thread-1/ensure")

    assert response.status_code == 200
    body = response.json()
    assert body["created"] is True
    assert isinstance(body["container_id"], str) and body["container_id"]


async def test_ensure_again_reports_created_false(client: AsyncClient) -> None:
    first = await client.post("/sessions/thread-1/ensure")
    second = await client.post("/sessions/thread-1/ensure")

    assert first.json()["created"] is True
    assert second.json()["created"] is False
    assert first.json()["container_id"] == second.json()["container_id"]


async def test_ensure_rejects_invalid_session_id_with_422(client: AsyncClient) -> None:
    response = await client.post("/sessions/not valid!/ensure")
    assert response.status_code == 422


async def test_ensure_accepts_uuid_style_session_id(client: AsyncClient) -> None:
    response = await client.post("/sessions/123e4567-e89b-12d3-a456-426614174000/ensure")
    assert response.status_code == 200


async def test_execute_on_nonexistent_session_returns_404(client: AsyncClient) -> None:
    response = await client.post("/sessions/never-ensured/execute", json={"command": "echo hi"})
    assert response.status_code == 404


async def test_execute_happy_path(client: AsyncClient, fake_docker: FakeDockerClient) -> None:
    await client.post("/sessions/thread-1/ensure")
    container = fake_docker.containers.get(container_name("thread-1"))
    container.exec_run_result = FakeExecResult(0, stdout=b"42\n", stderr=b"")

    response = await client.post(
        "/sessions/thread-1/execute", json={"command": "python3 -c 'print(6*7)'"}
    )

    assert response.status_code == 200
    body = response.json()
    assert body == {
        "stdout": "42\n",
        "stderr": "",
        "exit_code": 0,
        "timed_out": False,
        "duration_ms": body["duration_ms"],
        "truncated": False,
    }


async def test_execute_falls_back_to_default_timeout_when_unset(
    client: AsyncClient, fake_docker: FakeDockerClient, test_settings
) -> None:
    await client.post("/sessions/thread-1/ensure")
    container = fake_docker.containers.get(container_name("thread-1"))

    await client.post("/sessions/thread-1/execute", json={"command": "echo hi"})

    assert container.last_exec_cmd[3] == f"{test_settings.exec_default_timeout_s}s"


async def test_execute_honors_explicit_timeout_seconds(
    client: AsyncClient, fake_docker: FakeDockerClient
) -> None:
    await client.post("/sessions/thread-1/ensure")
    container = fake_docker.containers.get(container_name("thread-1"))

    await client.post("/sessions/thread-1/execute", json={"command": "echo hi", "timeout_seconds": 3})

    assert container.last_exec_cmd[3] == "3s"


async def test_delete_returns_204_and_removes_container(
    client: AsyncClient, fake_docker: FakeDockerClient
) -> None:
    await client.post("/sessions/thread-1/ensure")
    container = fake_docker.containers.get(container_name("thread-1"))

    response = await client.delete("/sessions/thread-1")

    assert response.status_code == 204
    assert container.removed is True


async def test_delete_is_idempotent_for_unknown_session(client: AsyncClient) -> None:
    response = await client.delete("/sessions/never-existed")
    assert response.status_code == 204


async def test_list_sessions_returns_active_sessions(client: AsyncClient) -> None:
    await client.post("/sessions/thread-1/ensure")
    await client.post("/sessions/thread-2/ensure")

    response = await client.get("/sessions")

    assert response.status_code == 200
    session_ids = {entry["session_id"] for entry in response.json()}
    assert session_ids == {"thread-1", "thread-2"}
    for entry in response.json():
        assert "container_id" in entry
        assert "last_used" in entry

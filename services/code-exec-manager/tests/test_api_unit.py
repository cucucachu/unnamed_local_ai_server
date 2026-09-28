"""HTTP-level unit tests for the four §7 endpoints (`app/api.py`), against
`FakeDockerClient` via `create_app(docker_client_override=...)` - no real
Docker daemon required. Delegations are signed with a test key the app's
real `JwksDelegationVerifier` trusts (`tests/conftest.py::Issuer`); grants
come from `FakeGrantsClient`.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from httpx import AsyncClient

from app.grants import GrantsDenied, GrantsUnavailable
from app.sessions import container_name
from tests.conftest import USER_A, USER_B, FakeGrantsClient, Issuer, grants_for
from tests.fake_docker import FakeDockerClient, FakeExecResult

T1 = "thread-1"


async def test_ensure_returns_201_like_200_with_created_true_first_time(
    client: AsyncClient, issuer: Issuer
) -> None:
    response = await client.post(f"/sessions/{T1}/ensure", headers=issuer.headers(T1))

    assert response.status_code == 200
    body = response.json()
    assert body["created"] is True
    assert isinstance(body["container_id"], str) and body["container_id"]


async def test_ensure_again_reports_created_false(client: AsyncClient, issuer: Issuer) -> None:
    first = await client.post(f"/sessions/{T1}/ensure", headers=issuer.headers(T1))
    second = await client.post(f"/sessions/{T1}/ensure", headers=issuer.headers(T1))

    assert first.json()["created"] is True
    assert second.json()["created"] is False
    assert first.json()["container_id"] == second.json()["container_id"]


async def test_ensure_rejects_invalid_session_id_with_422(
    client: AsyncClient, issuer: Issuer
) -> None:
    response = await client.post("/sessions/not valid!/ensure", headers=issuer.headers(T1))
    assert response.status_code == 422


async def test_ensure_accepts_uuid_style_session_id(client: AsyncClient, issuer: Issuer) -> None:
    thread = "123e4567-e89b-12d3-a456-426614174000"
    response = await client.post(f"/sessions/{thread}/ensure", headers=issuer.headers(thread))
    assert response.status_code == 200


async def test_ensure_passes_the_delegation_to_the_grants_fetch(
    client: AsyncClient, issuer: Issuer, fake_grants: FakeGrantsClient, fake_docker
) -> None:
    token = issuer.token(T1, USER_B)
    await client.post(f"/sessions/{T1}/ensure", headers={"Authorization": f"Bearer {token}"})

    assert fake_grants.calls == [(token, USER_B)]
    container = fake_docker.containers.get(container_name(T1))
    assert container.labels["homeai.user"] == USER_B
    assert container.run_kwargs["user"] == "20002:30002"


async def test_execute_on_nonexistent_session_returns_404(
    client: AsyncClient, issuer: Issuer
) -> None:
    response = await client.post(
        "/sessions/never-ensured/execute",
        json={"command": "echo hi"},
        headers=issuer.headers("never-ensured"),
    )
    assert response.status_code == 404


async def test_execute_happy_path(
    client: AsyncClient, fake_docker: FakeDockerClient, issuer: Issuer
) -> None:
    await client.post(f"/sessions/{T1}/ensure", headers=issuer.headers(T1))
    container = fake_docker.containers.get(container_name(T1))
    container.exec_run_result = FakeExecResult(0, stdout=b"42\n", stderr=b"")

    response = await client.post(
        f"/sessions/{T1}/execute",
        json={"command": "python3 -c 'print(6*7)'"},
        headers=issuer.headers(T1),
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
    assert container.last_exec_user == "20001:30001"


async def test_execute_falls_back_to_default_timeout_when_unset(
    client: AsyncClient, fake_docker: FakeDockerClient, test_settings, issuer: Issuer
) -> None:
    await client.post(f"/sessions/{T1}/ensure", headers=issuer.headers(T1))
    container = fake_docker.containers.get(container_name(T1))

    await client.post(
        f"/sessions/{T1}/execute", json={"command": "echo hi"}, headers=issuer.headers(T1)
    )

    assert container.last_exec_cmd[3] == f"{test_settings.exec_default_timeout_s}s"


async def test_execute_honors_explicit_timeout_seconds(
    client: AsyncClient, fake_docker: FakeDockerClient, issuer: Issuer
) -> None:
    await client.post(f"/sessions/{T1}/ensure", headers=issuer.headers(T1))
    container = fake_docker.containers.get(container_name(T1))

    await client.post(
        f"/sessions/{T1}/execute",
        json={"command": "echo hi", "timeout_seconds": 3},
        headers=issuer.headers(T1),
    )

    assert container.last_exec_cmd[3] == "3s"


async def test_delete_returns_204_and_removes_container(
    client: AsyncClient, fake_docker: FakeDockerClient, issuer: Issuer
) -> None:
    await client.post(f"/sessions/{T1}/ensure", headers=issuer.headers(T1))
    container = fake_docker.containers.get(container_name(T1))

    response = await client.delete(f"/sessions/{T1}", headers=issuer.headers(T1))

    assert response.status_code == 204
    assert container.removed is True


async def test_delete_is_idempotent_for_unknown_session(
    client: AsyncClient, issuer: Issuer
) -> None:
    response = await client.delete(
        "/sessions/never-existed", headers=issuer.headers("never-existed")
    )
    assert response.status_code == 204


async def test_delete_of_another_users_session_is_refused(
    client: AsyncClient, fake_docker: FakeDockerClient, issuer: Issuer
) -> None:
    await client.post(f"/sessions/{T1}/ensure", headers=issuer.headers(T1, USER_A))

    response = await client.delete(f"/sessions/{T1}", headers=issuer.headers(T1, USER_B))

    assert response.status_code == 403
    assert fake_docker.containers.get(container_name(T1)).removed is False


async def test_list_sessions_returns_active_sessions(client: AsyncClient, issuer: Issuer) -> None:
    await client.post("/sessions/thread-1/ensure", headers=issuer.headers("thread-1"))
    await client.post("/sessions/thread-2/ensure", headers=issuer.headers("thread-2"))

    response = await client.get("/sessions")

    assert response.status_code == 200
    session_ids = {entry["session_id"] for entry in response.json()}
    assert session_ids == {"thread-1", "thread-2"}
    for entry in response.json():
        assert "container_id" in entry
        assert "last_used" in entry


# --- delegation required -----------------------------------------------------------


CALLS = [
    ("post", f"/sessions/{T1}/ensure", None),
    ("post", f"/sessions/{T1}/execute", {"command": "id"}),
    ("delete", f"/sessions/{T1}", None),
]


async def _send(client: AsyncClient, method: str, path: str, body, headers):
    if method == "post":
        return await client.post(path, json=body, headers=headers)
    return await client.delete(path, headers=headers)


def _bad_credentials(issuer: Issuer) -> dict[str, dict[str, str]]:
    good = issuer.token(T1)
    header, payload, signature = good.split(".")
    return {
        "missing": {},
        "empty-bearer": {"Authorization": "Bearer "},
        "basic": {"Authorization": f"Basic {good}"},
        "garbage": {"Authorization": "Bearer not-a-jwt"},
        "tampered-signature": {"Authorization": f"Bearer {header}.{payload}.{signature[:-2]}AA"},
        "other-key": issuer.headers(T1, key=Ed25519PrivateKey.generate()),
        "expired": issuer.headers(T1, ttl=timedelta(seconds=-5)),
        "identity-token": issuer.headers(T1, act="user"),
        "wrong-issuer": issuer.headers(T1, iss="someone-else"),
        "wrong-audience": issuer.headers(T1, aud="other"),
        "bad-sub": issuer.headers(T1, user_id="not-a-uuid"),
    }


@pytest.mark.parametrize(
    "case",
    [
        "missing",
        "empty-bearer",
        "basic",
        "garbage",
        "tampered-signature",
        "other-key",
        "expired",
        "identity-token",
        "wrong-issuer",
        "wrong-audience",
        "bad-sub",
    ],
)
async def test_missing_or_forged_delegation_is_401_and_touches_nothing(
    client: AsyncClient,
    issuer: Issuer,
    fake_docker: FakeDockerClient,
    fake_grants: FakeGrantsClient,
    case: str,
) -> None:
    await client.post(f"/sessions/{T1}/ensure", headers=issuer.headers(T1))
    container = fake_docker.containers.get(container_name(T1))
    fake_grants.calls.clear()
    headers = _bad_credentials(issuer)[case]

    for method, path, body in CALLS:
        response = await _send(client, method, path, body, headers)
        assert (response.status_code, response.json()) == (401, {"detail": "unauthenticated"})

    assert fake_grants.calls == []
    assert container.removed is False
    assert container.last_exec_cmd is None
    assert len(fake_docker.containers.run_calls) == 1


async def test_threadless_delegation_is_401(client: AsyncClient, issuer: Issuer) -> None:
    token = issuer.token(T1, thr=None)
    response = await client.post(
        f"/sessions/{T1}/ensure", headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 401


async def test_another_threads_delegation_is_403(
    client: AsyncClient, issuer: Issuer, fake_docker: FakeDockerClient
) -> None:
    await client.post(f"/sessions/{T1}/ensure", headers=issuer.headers(T1))
    container = fake_docker.containers.get(container_name(T1))

    for method, path, body in CALLS:
        response = await _send(client, method, path, body, issuer.headers("thread-2"))
        assert response.status_code == 403

    assert container.removed is False
    assert container.last_exec_cmd is None


async def test_platform_refusing_the_delegation_is_401(
    client: AsyncClient, issuer: Issuer, fake_grants: FakeGrantsClient, fake_docker
) -> None:
    fake_grants.error = GrantsDenied("session revoked")

    ensure = await client.post(f"/sessions/{T1}/ensure", headers=issuer.headers(T1))
    execute = await client.post(
        f"/sessions/{T1}/execute", json={"command": "id"}, headers=issuer.headers(T1)
    )

    assert ensure.status_code == execute.status_code == 401
    assert fake_docker.containers.run_calls == []


async def test_platform_unavailable_is_503(
    client: AsyncClient, issuer: Issuer, fake_grants: FakeGrantsClient, fake_docker
) -> None:
    fake_grants.error = GrantsUnavailable("down")

    response = await client.post(f"/sessions/{T1}/ensure", headers=issuer.headers(T1))

    assert response.status_code == 503
    assert fake_docker.containers.run_calls == []


async def test_grant_change_between_calls_recreates_the_container(
    client: AsyncClient, issuer: Issuer, fake_grants: FakeGrantsClient, fake_docker
) -> None:
    first = await client.post(f"/sessions/{T1}/ensure", headers=issuer.headers(T1))
    old = fake_docker.containers.get(container_name(T1))
    fake_grants.by_user[USER_A] = grants_for(USER_A, shared=False)

    second = await client.post(f"/sessions/{T1}/ensure", headers=issuer.headers(T1))

    assert second.json()["created"] is True
    assert second.json()["container_id"] == first.json()["container_id"]  # fake ids are by name
    assert old.removed is True
    mounts = fake_docker.containers.run_calls[-1]["mounts"]
    assert [m["Target"] for m in mounts] == ["/files/personal"]


async def test_jwks_unavailable_is_503(fake_docker, test_settings, fake_grants) -> None:
    from httpx import ASGITransport

    from app.delegation import JwksDelegationVerifier
    from app.main import create_app

    async def broken() -> dict:
        raise RuntimeError("platform down")

    app = create_app(
        test_settings,
        docker_client_override=fake_docker,
        delegation_verifier=JwksDelegationVerifier(broken),
        grants_client=fake_grants,
    )
    transport = ASGITransport(app=app)
    async with (
        app.router.lifespan_context(app),
        AsyncClient(transport=transport, base_url="http://t") as client,
    ):
        response = await client.post(f"/sessions/{T1}/ensure", headers=Issuer().headers(T1))
    assert response.status_code == 503

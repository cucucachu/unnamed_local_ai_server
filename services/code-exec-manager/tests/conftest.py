from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from jwt.algorithms import OKPAlgorithm

from app.core.config import Settings
from app.delegation import JwksDelegationVerifier
from app.grants import Grants, GrantsDenied, Mount
from app.main import create_app
from app.sessions import SessionManager
from tests.fake_docker import FakeDockerClient

USER_A = "11111111-1111-4111-8111-111111111111"
USER_B = "22222222-2222-4222-8222-222222222222"
KID = "test-key"
SERVICE_TOKEN = "test-service-token"


def grants_for(user_id: str, uid: int = 20001, gid: int = 30001, shared: bool = True) -> Grants:
    mounts = [Mount(f"/srv/homeai/spaces/p-{uid}/files", "/files/personal", False)]
    gids = [gid]
    if shared:
        mounts.append(Mount("/srv/homeai/spaces/fam/files", "/files/spaces/family", True))
        gids.append(30050)
    return Grants(user_id, uid, gid, tuple(gids), tuple(mounts))


class Issuer:
    """Signs platform-shaped tokens with a test key the app's verifier trusts."""

    def __init__(self) -> None:
        self.key = Ed25519PrivateKey.generate()
        jwk = OKPAlgorithm.to_jwk(self.key.public_key(), as_dict=True)
        self.jwks = {"keys": [jwk | {"kid": KID, "use": "sig", "alg": "EdDSA"}]}

    def token(
        self,
        thread_id: str,
        user_id: str = USER_A,
        *,
        act: str = "agent",
        ttl: timedelta = timedelta(minutes=15),
        key: Ed25519PrivateKey | None = None,
        **extra: Any,
    ) -> str:
        now = datetime.now(UTC)
        claims = {
            "iss": "homeai-platform",
            "aud": "homeai",
            "sub": user_id,
            "sid": "33333333-3333-4333-8333-333333333333",
            "role": "member",
            "act": act,
            "thr": thread_id,
            "iat": now,
            "exp": now + ttl,
        } | extra
        return jwt.encode(claims, key or self.key, algorithm="EdDSA", headers={"kid": KID})

    def headers(self, thread_id: str, user_id: str = USER_A, **kw: Any) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token(thread_id, user_id, **kw)}"}


class FakeGrantsClient:
    def __init__(self) -> None:
        self.by_user: dict[str, Grants] = {
            USER_A: grants_for(USER_A),
            USER_B: grants_for(USER_B, uid=20002, gid=30002, shared=False),
        }
        self.error: Exception | None = None
        self.calls: list[tuple[str, str]] = []

    async def fetch(self, delegation: str, user_id: str) -> Grants:
        self.calls.append((delegation, user_id))
        if self.error is not None:
            raise self.error
        if user_id not in self.by_user:
            raise GrantsDenied("unknown user")
        return self.by_user[user_id]


@pytest.fixture
def test_settings() -> Settings:
    return Settings(
        exec_idle_minutes=30,
        exec_default_timeout_s=5,
        toolbox_image="homeai-exec-toolbox:latest",
        platform_exec_token=SERVICE_TOKEN,
        app_builds_host_dir="/srv/homeai/builds",
        _env_file=None,
    )


@pytest.fixture
def grants() -> Grants:
    return grants_for(USER_A)


@pytest.fixture
def fake_docker() -> FakeDockerClient:
    return FakeDockerClient()


@pytest.fixture
def manager(fake_docker: FakeDockerClient, test_settings: Settings) -> SessionManager:
    return SessionManager(fake_docker, test_settings)


@pytest.fixture
def issuer() -> Issuer:
    return Issuer()


@pytest.fixture
def fake_grants() -> FakeGrantsClient:
    return FakeGrantsClient()


@pytest.fixture
async def app(
    fake_docker: FakeDockerClient,
    test_settings: Settings,
    issuer: Issuer,
    fake_grants: FakeGrantsClient,
) -> AsyncIterator[FastAPI]:
    # `docker_client_override` keeps this off a real Docker socket (fast, no
    # real-Docker dependency) - `tests/test_sessions_integration.py` covers
    # the real SDK. The lifespan is run explicitly (not just wrapped in an
    # `ASGITransport`, which never sends "lifespan" scope messages) so
    # `app.state.session_manager` actually exists - same pattern as
    # agent-server's `tests/test_chat.py::rest_app` fixture.
    async def jwks() -> dict[str, Any]:
        return issuer.jwks

    application = create_app(
        test_settings,
        docker_client_override=fake_docker,
        delegation_verifier=JwksDelegationVerifier(jwks),
        grants_client=fake_grants,
    )
    async with application.router.lifespan_context(application):
        yield application


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        yield ac

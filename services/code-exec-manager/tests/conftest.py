from collections.abc import AsyncIterator

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.core.config import Settings
from app.main import create_app
from app.sessions import SessionManager
from tests.fake_docker import FakeDockerClient


@pytest.fixture
def test_settings(tmp_path) -> Settings:
    return Settings(
        workspace_host_dir=str(tmp_path),
        homeai_uid=1000,
        homeai_gid=1000,
        exec_idle_minutes=30,
        exec_default_timeout_s=5,
        toolbox_image="homeai-exec-toolbox:latest",
        _env_file=None,
    )


@pytest.fixture
def fake_docker() -> FakeDockerClient:
    return FakeDockerClient()


@pytest.fixture
def manager(fake_docker: FakeDockerClient, test_settings: Settings) -> SessionManager:
    return SessionManager(fake_docker, test_settings)


@pytest.fixture
async def app(fake_docker: FakeDockerClient, test_settings: Settings) -> AsyncIterator[FastAPI]:
    # `docker_client_override` keeps this off a real Docker socket (fast, no
    # real-Docker dependency) - `tests/test_sessions_integration.py` covers
    # the real SDK. The lifespan is run explicitly (not just wrapped in an
    # `ASGITransport`, which never sends "lifespan" scope messages) so
    # `app.state.session_manager` actually exists - same pattern as
    # agent-server's `tests/test_chat.py::rest_app` fixture.
    application = create_app(test_settings, docker_client_override=fake_docker)
    async with application.router.lifespan_context(application):
        yield application


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        yield ac

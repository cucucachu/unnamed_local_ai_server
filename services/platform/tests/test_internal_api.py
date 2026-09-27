from pathlib import Path

import pytest

from app.main import create_app
from tests.conftest import Platform, make_settings, running
from tests.helpers import bearer, bootstrap_admin, create_user, login

AGENT_TOKEN = "agent-secret"
EXEC_TOKEN = "exec-secret"


@pytest.fixture
async def platform(pg_database, tmp_path: Path):
    settings = make_settings(
        pg_database, tmp_path, platform_agent_token=AGENT_TOKEN, platform_exec_token=EXEC_TOKEN
    )
    app = create_app(settings)
    async with running(app) as client:
        yield Platform(app, client, pg_database, tmp_path)


async def test_bootstrap_admin_pending_then_set(platform):
    pending = await platform.client.get("/internal/bootstrap-admin", headers=bearer(AGENT_TOKEN))
    assert (pending.status_code, pending.json()) == (404, {"detail": "bootstrap_pending"})

    await create_user(platform, "cli-admin", role="admin")
    still = await platform.client.get("/internal/bootstrap-admin", headers=bearer(AGENT_TOKEN))
    assert still.status_code == 404

    token = await bootstrap_admin(platform)
    me = await platform.client.get("/internal/auth/verify", headers=bearer(token))
    assert me.status_code == 200
    response = await platform.client.get("/internal/bootstrap-admin", headers=bearer(AGENT_TOKEN))
    assert response.status_code == 200
    user_id = response.json()["user_id"]
    listed = await platform.client.get(
        "/api/platform/me",
        headers={"X-HomeAI-Identity": me.headers["X-HomeAI-Identity"]},
    )
    assert listed.json()["id"] == user_id


@pytest.mark.parametrize(
    "headers",
    [
        {},
        bearer(EXEC_TOKEN),
        bearer("agent-secre"),
        bearer(AGENT_TOKEN + "x"),
        {"Authorization": f"Basic {AGENT_TOKEN}"},
    ],
)
async def test_bootstrap_admin_requires_agent_token(platform, headers):
    response = await platform.client.get("/internal/bootstrap-admin", headers=headers)
    assert (response.status_code, response.json()) == (401, {"detail": "unauthenticated"})


async def test_bootstrap_admin_rejects_session_credentials(platform):
    await create_user(platform, "member")
    session = await login(platform, "member")
    response = await platform.client.get("/internal/bootstrap-admin", headers=bearer(session))
    assert response.status_code == 401


async def test_unset_service_token_never_matches(pg_database, tmp_path):
    app = create_app(make_settings(pg_database, tmp_path))
    async with running(app) as client:
        for headers in ({}, {"Authorization": "Bearer "}, bearer("")):
            response = await client.get("/internal/bootstrap-admin", headers=headers)
            assert response.status_code == 401

"""`/internal/space-access`: a user's role in a space, for agent-server's routines (M17-02)."""

from pathlib import Path

import pytest

from app.main import create_app
from tests.conftest import Platform, make_settings, running
from tests.helpers import bearer, create_user, identity, login, sql

AGENT_TOKEN = "agent-secret"
API = "/api/platform"


@pytest.fixture
async def platform(pg_database, tmp_path: Path):
    settings = make_settings(pg_database, tmp_path, platform_agent_token=AGENT_TOKEN)
    app = create_app(settings)
    async with running(app) as client:
        yield Platform(app, client, pg_database, tmp_path)


async def _user(platform, username: str) -> tuple[dict, dict[str, str]]:
    user = await create_user(platform, username)
    return user, await identity(platform, await login(platform, username))


async def _access(platform, headers: dict[str, str], space: str, service: str = AGENT_TOKEN):
    return await platform.client.post(
        "/internal/space-access",
        json={"identity_token": headers["X-HomeAI-Identity"], "space": space},
        headers=bearer(service),
    )


async def _family(platform, owner_headers, member_id=None, role="editor") -> None:
    response = await platform.client.post(
        f"{API}/spaces", json={"slug": "family", "name": "Family"}, headers=owner_headers
    )
    assert response.status_code == 201, response.text
    if member_id is not None:
        space_id = response.json()["id"]
        added = await platform.client.post(
            f"{API}/spaces/{space_id}/members",
            json={"user_id": str(member_id), "role": role},
            headers=owner_headers,
        )
        assert added.status_code == 201, added.text


async def test_personal_space_is_owned(platform):
    _, alice = await _user(platform, "alice")
    response = await _access(platform, alice, "/personal/")
    assert response.status_code == 200, response.text
    assert response.json() == {"space": "/personal", "role": "owner"}


async def test_shared_space_roles(platform):
    _, alice = await _user(platform, "alice")
    bob, bob_headers = await _user(platform, "bob")
    _, carol_headers = await _user(platform, "carol")
    await _family(platform, alice, bob["id"], role="viewer")

    assert (await _access(platform, alice, "/spaces/family")).json()["role"] == "owner"
    assert (await _access(platform, bob_headers, "spaces/Family")).json() == {
        "space": "/spaces/family",
        "role": "viewer",
    }
    assert (await _access(platform, carol_headers, "/spaces/family")).status_code == 404
    assert (await _access(platform, carol_headers, "/spaces/nope")).status_code == 404


@pytest.mark.parametrize("space", ["/", "/spaces", "/personal/notes", "/elsewhere", "/spaces/../x"])
async def test_only_a_space_root_names_a_space(platform, space):
    _, alice = await _user(platform, "alice")
    assert (await _access(platform, alice, space)).status_code == 422


async def test_needs_the_agent_service_and_a_live_session(platform):
    alice, headers = await _user(platform, "alice")
    assert (await _access(platform, headers, "/personal", service="wrong")).status_code == 401

    sql(platform, "UPDATE sessions SET revoked_at = now() WHERE user_id = %s", (alice["id"],))
    assert (await _access(platform, headers, "/personal")).status_code == 401

    response = await platform.client.post(
        "/internal/space-access",
        json={"identity_token": "not-a-token", "space": "/personal"},
        headers=bearer(AGENT_TOKEN),
    )
    assert response.status_code == 401

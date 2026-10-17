"""The admin's switch for delivered routines (M21-08, docs/MEMORY.md §4.4)."""

from pathlib import Path

import pytest

from app.main import create_app
from tests.conftest import Platform, make_settings, running
from tests.helpers import (
    bearer,
    bootstrap_admin,
    create_user,
    delegation,
    identity,
    login,
    sql,
    step_up,
)

AGENT_TOKEN = "agent-secret"
API = "/api/platform"
CATALOG = [
    {"key": "memory", "name": "Memory", "description": "Keeps notes from chats."},
    {"key": "tidy", "name": "Tidy", "description": "Tidies files."},
]


@pytest.fixture
async def platform(pg_database, tmp_path: Path):
    app = create_app(make_settings(pg_database, tmp_path, platform_agent_token=AGENT_TOKEN))
    async with running(app) as client:
        yield Platform(app, client, pg_database, tmp_path)


async def _register(platform, routines=CATALOG, token=AGENT_TOKEN):
    return await platform.client.put(
        "/internal/delivered-routines", json={"routines": routines}, headers=bearer(token)
    )


@pytest.fixture
async def people(platform):
    assert (await _register(platform)).status_code == 204
    admin_token = await bootstrap_admin(platform)
    await create_user(platform, "bob")
    bob_token = await login(platform, "bob")
    return {
        "admin_token": admin_token,
        "admin": await identity(platform, admin_token),
        "bob": await identity(platform, bob_token),
        "bob_agent": await delegation(platform, bob_token),
    }


async def _switches(platform, headers) -> dict[str, bool]:
    response = await platform.client.get(f"{API}/delivered-routines", headers=headers)
    assert response.status_code == 200, response.text
    return {r["key"]: r["enabled_by_admin"] for r in response.json()["routines"]}


async def _set(platform, headers, key: str, enabled: bool):
    return await platform.client.put(
        f"{API}/admin/delivered-routines/{key}", json={"enabled": enabled}, headers=headers
    )


async def test_registering_needs_the_agent_service_token(platform):
    assert (await _register(platform, token="wrong")).status_code == 401


async def test_every_routine_defaults_on_for_users_and_their_agents(platform, people):
    response = await platform.client.get(f"{API}/delivered-routines", headers=people["bob"])
    assert response.json()["routines"][0] == {
        "key": "memory",
        "name": "Memory",
        "description": "Keeps notes from chats.",
        "enabled_by_admin": True,
    }
    assert await _switches(platform, people["bob_agent"]) == {"memory": True, "tidy": True}


async def test_only_a_stepped_up_admin_can_change_it(platform, people):
    assert (await _set(platform, people["bob"], "memory", False)).status_code == 403
    assert (await _set(platform, people["bob_agent"], "memory", False)).status_code == 403
    assert (await _set(platform, people["admin"], "memory", False)).status_code == 403  # no step-up

    await step_up(platform, people["admin_token"])
    response = await _set(platform, people["admin"], "memory", False)
    assert response.status_code == 200, response.text
    assert response.json()["enabled_by_admin"] is False
    assert await _switches(platform, people["bob_agent"]) == {"memory": False, "tidy": True}
    assert (await _set(platform, people["admin"], "nope", False)).status_code == 404


async def test_it_needs_a_privileged_origin(platform, people):
    await step_up(platform, people["admin_token"])
    headers = {**people["admin"], "X-Forwarded-For": "8.8.8.8"}
    response = await _set(platform, headers, "memory", False)
    assert (response.status_code, response.json()) == (403, {"detail": "public_origin"})
    assert await _switches(platform, people["bob"]) == {"memory": True, "tidy": True}


async def test_changes_are_audited(platform, people):
    await step_up(platform, people["admin_token"])
    await _set(platform, people["admin"], "memory", False)
    await _set(platform, people["admin"], "memory", True)
    rows = sql(platform, "SELECT kind, summary, target_id FROM audit_events ORDER BY id")
    changes = [r for r in rows if r["kind"] == "delivered_routines.admin_changed"]
    assert [(r["summary"], r["target_id"]) for r in changes] == [
        ("Turned the Memory routine off for everyone", "memory"),
        ("Turned the Memory routine on for everyone", "memory"),
    ]


async def test_registering_again_keeps_the_switch(platform, people):
    await step_up(platform, people["admin_token"])
    await _set(platform, people["admin"], "memory", False)
    await _register(platform, CATALOG[1:])
    assert await _switches(platform, people["bob"]) == {"tidy": True}
    await _register(platform)
    assert await _switches(platform, people["bob"]) == {"memory": False, "tidy": True}


async def test_agent_server_reads_the_switches_with_its_service_token(platform, people):
    response = await platform.client.get(
        "/internal/delivered-routines", headers=bearer(AGENT_TOKEN)
    )
    assert response.status_code == 200
    assert [r["key"] for r in response.json()["routines"]] == ["memory", "tidy"]
    wrong = await platform.client.get("/internal/delivered-routines", headers=bearer("x"))
    assert wrong.status_code == 401

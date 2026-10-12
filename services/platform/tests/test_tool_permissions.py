"""Agent tool permissions (#342): catalog, the rule, user and admin settings."""

from pathlib import Path

import pytest

from app.core.tool_permissions import Tool, evaluate
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
    {"name": "read_file", "label": "Read files", "description": "", "group": "files", "read_only": True},
    {"name": "write_file", "label": "Write files", "description": "", "group": "files", "read_only": False},
    {"name": "execute_code", "label": "Run code", "description": "", "group": "code", "read_only": False},
]  # fmt: skip


@pytest.fixture
async def platform(pg_database, tmp_path: Path):
    app = create_app(make_settings(pg_database, tmp_path, platform_agent_token=AGENT_TOKEN))
    async with running(app) as client:
        yield Platform(app, client, pg_database, tmp_path)


@pytest.fixture
async def people(platform):
    """A registered catalog, a stepped-up admin and a member (identity headers)."""
    response = await platform.client.put(
        "/internal/tool-catalog", json={"tools": CATALOG}, headers=bearer(AGENT_TOKEN)
    )
    assert response.status_code == 204, response.text
    admin_token = await bootstrap_admin(platform)
    await step_up(platform, admin_token)
    await create_user(platform, "bob")
    bob_token = await login(platform, "bob")
    return {
        "admin": await identity(platform, admin_token),
        "bob": await identity(platform, bob_token),
        "bob_agent": await delegation(platform, bob_token),
    }


async def _mine(platform, headers) -> dict[str, dict]:
    response = await platform.client.get(f"{API}/tools", headers=headers)
    assert response.status_code == 200, response.text
    return {t["name"]: t for t in response.json()["tools"]}


async def _policy(platform, headers) -> dict[str, dict]:
    response = await platform.client.get(f"{API}/tools/policy", headers=headers)
    assert response.status_code == 200, response.text
    return response.json()["tools"]


async def _change(platform, headers, *changes, admin=False):
    path = f"{API}/admin/tools" if admin else f"{API}/tools"
    return await platform.client.patch(path, json={"changes": list(changes)}, headers=headers)


# --- the rule -------------------------------------------------------------------------

READ = Tool("read_file", "Read files", "", "files", True)
WRITE = Tool("write_file", "Write files", "", "files", False)


def test_defaults_ceiling_allow_user_ask_read_only_allow():
    assert evaluate(WRITE, None, None).permission == "ask"
    assert evaluate(READ, None, None).permission == "allow"
    assert evaluate(WRITE, None, None).available


@pytest.mark.parametrize("ceiling", [None, "allow", "ask"])
@pytest.mark.parametrize("mine", [None, "allow", "ask"])
def test_allow_only_if_both_allow(ceiling, mine):
    effective = evaluate(WRITE, {"ceiling": ceiling}, {"permission": mine})
    want = "allow" if ceiling != "ask" and mine == "allow" else "ask"
    assert effective.permission == want
    assert effective.permission_locked is (ceiling == "ask")


@pytest.mark.parametrize("admin_on", [None, True, False])
@pytest.mark.parametrize("user_on", [None, True, False])
def test_available_only_if_both_on(admin_on, user_on):
    effective = evaluate(WRITE, {"enabled": admin_on}, {"enabled": user_on})
    assert effective.available is (admin_on is not False and user_on is not False)
    assert effective.enabled_locked is (admin_on is False)


def test_a_read_only_tool_still_follows_the_ceiling():
    assert evaluate(READ, {"ceiling": "ask"}, None).permission == "ask"


# --- the API --------------------------------------------------------------------------


async def test_catalog_needs_the_agent_service_token(platform):
    response = await platform.client.put(
        "/internal/tool-catalog", json={"tools": CATALOG}, headers=bearer("wrong")
    )
    assert response.status_code == 401


async def test_defaults_for_a_new_user(platform, people):
    mine = await _mine(platform, people["bob"])
    assert [t for t in mine] == ["read_file", "write_file", "execute_code"]
    assert mine["read_file"]["permission"] == "allow"
    assert mine["write_file"] | {"label": None} == {
        "name": "write_file",
        "label": None,
        "description": "",
        "group": "files",
        "read_only": False,
        "enabled": True,
        "permission": "ask",
        "enabled_locked": False,
        "permission_locked": False,
    }


async def test_a_user_sets_their_own(platform, people):
    response = await _change(
        platform,
        people["bob"],
        {"name": "write_file", "permission": "allow"},
        {"name": "execute_code", "enabled": False},
    )
    assert response.status_code == 200, response.text
    policy = await _policy(platform, people["bob_agent"])
    assert policy["write_file"] == {
        "available": True,
        "permission": "allow",
        "can_always_allow": True,
    }
    assert policy["execute_code"]["available"] is False
    # Someone else's are untouched.
    admin = await _policy(platform, people["admin"])
    assert admin["write_file"]["permission"] == "ask"
    assert admin["execute_code"]["available"] is True


async def test_the_agent_can_read_its_policy_but_change_nothing(platform, people):
    assert (await _policy(platform, people["bob_agent"]))["write_file"]["permission"] == "ask"
    response = await _change(
        platform, people["bob_agent"], {"name": "write_file", "permission": "allow"}
    )
    assert response.status_code == 403
    assert response.json()["detail"] == "agent_not_allowed"
    assert (
        await platform.client.get(f"{API}/tools", headers=people["bob_agent"])
    ).status_code == 403
    response = await _change(
        platform, people["bob_agent"], {"name": "write_file", "ceiling": "allow"}, admin=True
    )
    assert response.status_code == 403


async def test_unknown_tools_and_fields_are_refused(platform, people):
    response = await _change(
        platform, people["bob"], {"name": "launch_rockets", "permission": "allow"}
    )
    assert response.status_code == 422
    assert response.json()["detail"] == "unknown_tool"
    response = await _change(platform, people["bob"], {"name": "write_file", "ceiling": "allow"})
    assert response.status_code == 422


async def test_members_cant_use_the_admin_settings(platform, people):
    response = await _change(
        platform, people["bob"], {"name": "write_file", "ceiling": "ask"}, admin=True
    )
    assert response.status_code == 403
    assert (
        await platform.client.get(f"{API}/admin/tools", headers=people["bob"])
    ).status_code == 403


async def test_an_admin_tightening_wins_without_rewriting_users(platform, people):
    await _change(platform, people["bob"], {"name": "write_file", "permission": "allow"})
    response = await _change(
        platform,
        people["admin"],
        {"name": "write_file", "ceiling": "ask"},
        {"name": "execute_code", "enabled": False},
        admin=True,
    )
    assert response.status_code == 200, response.text
    tools = {t["name"]: t for t in response.json()["tools"]}
    assert tools["write_file"]["ceiling"] == "ask"
    assert tools["execute_code"]["enabled"] is False

    mine = await _mine(platform, people["bob"])
    assert mine["write_file"]["permission"] == "ask"
    assert mine["write_file"]["permission_locked"] is True
    assert mine["execute_code"]["enabled"] is False
    assert mine["execute_code"]["enabled_locked"] is True
    policy = await _policy(platform, people["bob_agent"])
    assert policy["write_file"] == {
        "available": True,
        "permission": "ask",
        "can_always_allow": False,
    }
    assert policy["execute_code"]["available"] is False
    # Bob's stored allow is still there; relaxing the ceiling brings it back.
    rows = sql(
        platform, "SELECT permission FROM agent_tool_user_settings WHERE tool = 'write_file'"
    )
    assert rows == [{"permission": "allow"}]
    await _change(platform, people["admin"], {"name": "write_file", "ceiling": "allow"}, admin=True)
    assert (await _policy(platform, people["bob_agent"]))["write_file"]["permission"] == "allow"


async def test_a_user_cant_go_past_the_admin(platform, people):
    await _change(
        platform,
        people["admin"],
        {"name": "write_file", "ceiling": "ask"},
        {"name": "execute_code", "enabled": False},
        admin=True,
    )
    for change in (
        {"name": "write_file", "permission": "allow"},
        {"name": "execute_code", "enabled": True},
    ):
        response = await _change(platform, people["bob"], change)
        assert response.status_code == 422
        assert response.json()["detail"] == "locked_by_admin"
    # All or nothing: an allowed change next to a refused one isn't applied.
    response = await _change(
        platform,
        people["bob"],
        {"name": "read_file", "enabled": False},
        {"name": "write_file", "permission": "allow"},
    )
    assert response.status_code == 422
    assert (await _mine(platform, people["bob"]))["read_file"]["enabled"] is True
    # Asking (stricter than the ceiling) is always fine.
    response = await _change(platform, people["bob"], {"name": "write_file", "permission": "ask"})
    assert response.status_code == 200


async def test_replacing_the_catalog_keeps_settings(platform, people):
    await _change(platform, people["bob"], {"name": "execute_code", "permission": "allow"})
    response = await platform.client.put(
        "/internal/tool-catalog", json={"tools": CATALOG[:2]}, headers=bearer(AGENT_TOKEN)
    )
    assert response.status_code == 204
    assert "execute_code" not in await _policy(platform, people["bob_agent"])
    await platform.client.put(
        "/internal/tool-catalog", json={"tools": CATALOG}, headers=bearer(AGENT_TOKEN)
    )
    assert (await _policy(platform, people["bob_agent"]))["execute_code"]["permission"] == "allow"


async def test_changes_are_audited(platform, people):
    await _change(platform, people["bob"], {"name": "write_file", "permission": "allow"})
    await _change(platform, people["admin"], {"name": "execute_code", "ceiling": "ask"}, admin=True)
    rows = sql(platform, "SELECT kind, actor_name, summary FROM audit_events ORDER BY id")
    kinds = [(r["kind"], r["summary"]) for r in rows if r["kind"].startswith("tools.")]
    assert kinds == [
        ("tools.settings_changed", "Changed my tool settings: Write files: allow"),
        ("tools.admin_changed", "Changed tool settings for everyone: Run code: ask"),
    ]

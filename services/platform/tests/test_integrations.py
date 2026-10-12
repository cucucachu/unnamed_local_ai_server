"""Integrations registry (M18-03): install, discovery, tool settings, credentials, grants."""

import base64
import json
from pathlib import Path
from uuid import UUID

import pytest

from app.core import integrations
from app.core.errors import InvalidInput
from app.core.integrations import check_url
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

GATEWAY_TOKEN = "gateway-secret"
AGENT_TOKEN = "agent-secret"
API = "/api/platform"
ADMIN = f"{API}/admin/integrations"
SECRET = "hunter2-long-lived-token"


def _tool(name, **extra):
    return {"name": name, "input_schema": {"type": "object"}, **extra}


DISCOVERY = {
    "protocol_version": "2026-07-28",
    "server": {"name": "fake-home", "version": "1.0", "description": "Lights and switches"},
    "instructions": "Use turn_on for lights.",
    "tools": [
        _tool("get_state", title="Get state", annotations={"readOnlyHint": True}),
        _tool("turn_on", description="Turn something on"),
    ],
    "resources": [{"uri": "home://rooms", "name": "Rooms"}],
    "prompts": [{"name": "morning", "arguments": [{"name": "room"}]}],
}


class FakeGateway:
    def __init__(self) -> None:
        self.discovery = DISCOVERY
        self.encrypted: list[tuple] = []
        self.discovered: list[tuple] = []

    async def encrypt(self, integration_id, user_id, secret) -> bytes:
        self.encrypted.append((integration_id, user_id, secret))
        return b"sealed:" + str(user_id).encode()

    async def discover(self, integration, credential):
        self.discovered.append((integration, credential))
        return self.discovery


@pytest.fixture
async def platform(pg_database, tmp_path: Path):
    app = create_app(
        make_settings(
            pg_database,
            tmp_path,
            platform_gateway_token=GATEWAY_TOKEN,
            platform_agent_token=AGENT_TOKEN,
        )
    )
    async with running(app) as client:
        app.state.gateway = FakeGateway()
        yield Platform(app, client, pg_database, tmp_path)


@pytest.fixture
async def people(platform):
    admin_token = await bootstrap_admin(platform)
    await step_up(platform, admin_token)
    await create_user(platform, "bob")
    await create_user(platform, "carol")
    bob_token, carol_token = await login(platform, "bob"), await login(platform, "carol")
    return {
        "admin": await identity(platform, admin_token),
        "bob": await identity(platform, bob_token),
        "bob_agent": await delegation(platform, bob_token),
        "carol_agent": await delegation(platform, carol_token),
    }


def _install_body(**overrides):
    return {
        "slug": "home",
        "name": "Home",
        "url": "https://ha.example.com/mcp",
        "credential_model": "per_user",
        "auth_method": "token",
        **overrides,
    }


async def _install(platform, people, **overrides) -> dict:
    response = await platform.client.post(
        ADMIN, json=_install_body(**overrides), headers=people["admin"]
    )
    assert response.status_code == 201, response.text
    return response.json()


async def _refresh(platform, people, iid) -> dict:
    response = await platform.client.post(f"{ADMIN}/{iid}/refresh", headers=people["admin"])
    assert response.status_code == 200, response.text
    return response.json()


async def _installed(platform, people, **overrides) -> str:
    iid = (await _install(platform, people, **overrides))["id"]
    await _refresh(platform, people, iid)
    return iid


async def _mine(platform, headers) -> dict[str, dict]:
    response = await platform.client.get(f"{API}/integrations", headers=headers)
    assert response.status_code == 200, response.text
    return {i["slug"]: i for i in response.json()["integrations"]}


def _tools(integration) -> dict[str, dict]:
    return {t["name"]: t for t in integration["tools"]}


async def _admin_tools(platform, people, iid, *changes):
    response = await platform.client.patch(
        f"{ADMIN}/{iid}/tools", json={"changes": list(changes)}, headers=people["admin"]
    )
    assert response.status_code == 200, response.text
    return {t["name"]: t for t in response.json()["tools"]}


async def _grant(platform, agent_headers, slug="home", token=GATEWAY_TOKEN):
    return await platform.client.post(
        "/internal/integration-grants",
        json={"delegation": agent_headers["Authorization"].split()[1], "integration": slug},
        headers=bearer(token),
    )


async def _connect(platform, headers, iid, secret=SECRET):
    return await platform.client.put(
        f"{API}/integrations/{iid}/connection", json={"secret": secret}, headers=headers
    )


# --- addresses ------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("url", "private", "code"),
    [
        ("http://ha.example.com/mcp", False, "https_required"),
        ("https://192.168.0.20:8123/mcp", False, "private_address_needs_exception"),
        ("https://[fd00::5]/mcp", False, "private_address_needs_exception"),
        ("https://169.254.1.1/mcp", False, "private_address_needs_exception"),
        ("http://127.0.0.1:8123/mcp", True, "loopback_refused"),
        ("http://localhost:8123/mcp", True, "loopback_refused"),
        ("https://[::1]/mcp", True, "loopback_refused"),
        ("http://0.0.0.0/mcp", True, "loopback_refused"),
        ("http://homeassistant.local:8123/mcp", True, "mdns_name"),
        ("ftp://ha.example.com/mcp", False, "invalid_url"),
        ("https://user:pw@ha.example.com/mcp", False, "invalid_url"),
        ("https:///mcp", False, "invalid_url"),
        ("https://ha.example.com:99999/mcp", False, "invalid_url"),
    ],
)
def test_addresses_that_are_refused(url, private, code):
    with pytest.raises(InvalidInput) as exc:
        check_url(url, private)
    assert exc.value.code == code


def test_addresses_that_are_accepted():
    assert check_url("https://ha.example.com/mcp", False).port == 443
    exception = check_url("http://192.168.0.20:8123/mcp", True)
    assert (exception.scheme, exception.host, exception.port) == ("http", "192.168.0.20", 8123)
    assert check_url("https://8.8.8.8/mcp", False).host == "8.8.8.8"


# --- who may do what ------------------------------------------------------------------


async def test_members_cant_install_or_change_settings(platform, people):
    iid = (await _install(platform, people))["id"]
    bob = people["bob"]
    calls = [
        ("GET", ADMIN, None),
        ("POST", ADMIN, _install_body(slug="other")),
        ("PATCH", f"{ADMIN}/{iid}", {"name": "Mine"}),
        ("DELETE", f"{ADMIN}/{iid}", None),
        ("POST", f"{ADMIN}/{iid}/refresh", None),
        ("PATCH", f"{ADMIN}/{iid}/tools", {"changes": [{"name": "turn_on", "enabled": True}]}),
        ("PUT", f"{ADMIN}/{iid}/shared-credential", {"secret": SECRET}),
        ("DELETE", f"{ADMIN}/{iid}/shared-credential", None),
    ]
    for method, path, body in calls:
        response = await platform.client.request(method, path, json=body, headers=bob)
        assert response.status_code == 403, (method, path, response.text)
    assert len(sql(platform, "SELECT id FROM integrations")) == 1


async def test_the_agent_can_list_but_not_connect_or_change(platform, people):
    iid = await _installed(platform, people)
    agent = people["bob_agent"]
    assert "home" in await _mine(platform, agent)
    assert (await _connect(platform, agent, iid)).status_code == 403
    response = await platform.client.patch(
        f"{API}/integrations/{iid}/tools",
        json={"changes": [{"name": "turn_on", "permission": "allow"}]},
        headers=agent,
    )
    assert response.status_code == 403
    response = await platform.client.delete(f"{API}/integrations/{iid}/connection", headers=agent)
    assert response.status_code == 403


async def test_internal_routes_need_the_gateway_token(platform, people):
    iid = (await _install(platform, people))["id"]
    assert (await _grant(platform, people["bob_agent"], token=AGENT_TOKEN)).status_code == 401
    response = await platform.client.put(
        f"/internal/integrations/{iid}/discovery", json=DISCOVERY, headers=bearer(AGENT_TOKEN)
    )
    assert response.status_code == 401
    response = await platform.client.post(
        f"/internal/integrations/{iid}/credential-status",
        json={"status": "needs_reconnecting"},
        headers=bearer("wrong"),
    )
    assert response.status_code == 401


# --- installing ----------------------------------------------------------------------


async def test_install_checks_the_address_and_the_auth_setup(platform, people):
    async def install(**overrides):
        return await platform.client.post(
            ADMIN, json=_install_body(**overrides), headers=people["admin"]
        )

    response = await install(url="http://ha.example.com/mcp")
    assert response.json()["detail"] == "https_required"
    response = await install(url="http://192.168.0.20:8123/mcp")
    assert response.json()["detail"] == "private_address_needs_exception"
    response = await install(credential_model="none")
    assert response.json()["detail"] == "auth_method_mismatch"
    response = await install(url="http://192.168.0.20:8123/mcp", private_address=True)
    assert response.status_code == 201, response.text
    assert response.json()["private_address"] is True
    assert response.json()["discovered_at"] is None
    assert (await install(name="Again")).status_code == 409


async def test_refresh_stores_what_the_server_offers(platform, people):
    iid = (await _install(platform, people))["id"]
    got = await _refresh(platform, people, iid)
    assert got["protocol_version"] == "2026-07-28"
    assert got["description"] == "Lights and switches"
    assert [t["name"] for t in got["tools"]] == ["get_state", "turn_on"]
    assert got["tools"][0]["annotations"] == {"readOnlyHint": True}
    assert got["resources"] == [
        {
            "uri": "home://rooms",
            "name": "Rooms",
            "title": None,
            "description": "",
            "mime_type": None,
        }
    ]
    assert got["prompts"][0]["arguments"] == [{"name": "room"}]
    ((integration, credential),) = platform.app.state.gateway.discovered
    assert integration["url"] == "https://ha.example.com/mcp"
    assert credential is None  # the admin hasn't connected


async def test_new_tools_start_off_for_the_admin(platform, people):
    iid = await _installed(platform, people)
    tools = _tools((await _mine(platform, people["bob"]))["home"])
    assert tools["turn_on"]["enabled"] is False
    assert tools["turn_on"]["enabled_locked"] is True

    await _admin_tools(platform, people, iid, {"name": "turn_on", "enabled": True})
    platform.app.state.gateway.discovery = {
        **DISCOVERY,
        "tools": [*DISCOVERY["tools"], _tool("turn_off")],
    }
    admin = {t["name"]: t for t in (await _refresh(platform, people, iid))["tools"]}
    assert admin["turn_on"]["enabled"] is True
    assert admin["turn_off"]["enabled"] is False


async def test_a_tool_that_goes_away_comes_back_off(platform, people):
    iid = await _installed(platform, people)
    await _admin_tools(platform, people, iid, {"name": "turn_on", "enabled": True})
    gateway = platform.app.state.gateway
    gateway.discovery = {**DISCOVERY, "tools": DISCOVERY["tools"][:1]}
    await _refresh(platform, people, iid)
    gateway.discovery = DISCOVERY
    admin = {t["name"]: t for t in (await _refresh(platform, people, iid))["tools"]}
    assert admin["turn_on"]["enabled"] is False


async def test_the_gateway_can_store_a_changed_list(platform, people):
    iid = await _installed(platform, people)
    response = await platform.client.put(
        f"/internal/integrations/{iid}/discovery",
        json={**DISCOVERY, "tools": [_tool("turn_off")]},
        headers=bearer(GATEWAY_TOKEN),
    )
    assert response.status_code == 204, response.text
    assert list(_tools((await _mine(platform, people["bob"]))["home"])) == ["turn_off"]
    response = await platform.client.put(
        f"/internal/integrations/{iid}/discovery",
        json={**DISCOVERY, "tools": [_tool("bad name!")]},
        headers=bearer(GATEWAY_TOKEN),
    )
    assert response.status_code == 422


async def test_refresh_without_a_gateway_is_unavailable(platform, people):
    iid = (await _install(platform, people))["id"]
    platform.app.state.gateway = integrations_gateway_unconfigured()
    response = await platform.client.post(f"{ADMIN}/{iid}/refresh", headers=people["admin"])
    assert response.status_code == 503
    assert response.json()["detail"] == "gateway_unavailable"


def integrations_gateway_unconfigured():
    from app.core.integrations_gateway import HttpGateway

    return HttpGateway("", "", 1.0)


# --- tool settings ---------------------------------------------------------------------


async def test_every_user_starts_at_ask_whatever_the_annotations(platform, people):
    iid = await _installed(platform, people)
    await _admin_tools(
        platform,
        people,
        iid,
        {"name": "get_state", "enabled": True},
        {"name": "turn_on", "enabled": True},
    )
    tools = _tools((await _mine(platform, people["bob"]))["home"])
    assert tools["get_state"]["permission"] == "ask"
    assert tools["turn_on"] | {"description": None} == {
        "name": "turn_on",
        "title": None,
        "description": None,
        "annotations": {},
        "enabled": True,
        "permission": "ask",
        "enabled_locked": False,
        "permission_locked": False,
    }


async def test_allow_only_if_both_allow(platform, people):
    iid = await _installed(platform, people)
    await _admin_tools(platform, people, iid, {"name": "turn_on", "enabled": True})
    response = await platform.client.patch(
        f"{API}/integrations/{iid}/tools",
        json={"changes": [{"name": "turn_on", "permission": "allow"}]},
        headers=people["bob"],
    )
    assert response.status_code == 200, response.text
    assert _tools(response.json()["integrations"][0])["turn_on"]["permission"] == "allow"

    await _admin_tools(platform, people, iid, {"name": "turn_on", "ceiling": "ask"})
    turn_on = _tools((await _mine(platform, people["bob"]))["home"])["turn_on"]
    assert (turn_on["permission"], turn_on["permission_locked"]) == ("ask", True)
    response = await platform.client.patch(
        f"{API}/integrations/{iid}/tools",
        json={"changes": [{"name": "get_state", "enabled": True}]},
        headers=people["bob"],
    )
    assert response.json()["detail"] == "locked_by_admin"  # the admin has it off
    response = await platform.client.patch(
        f"{API}/integrations/{iid}/tools",
        json={"changes": [{"name": "no_such_tool", "enabled": False}]},
        headers=people["bob"],
    )
    assert response.json()["detail"] == "unknown_tool"


async def test_integration_tools_stay_out_of_the_built_in_lists(platform, people):
    iid = await _installed(platform, people)
    await _admin_tools(platform, people, iid, {"name": "turn_on", "enabled": True})
    for path, headers in [
        (f"{API}/tools", people["bob"]),
        (f"{API}/tools/policy", people["bob_agent"]),
    ]:
        response = await platform.client.get(path, headers=headers)
        assert "mcp:" not in response.text


# --- credentials -----------------------------------------------------------------------


async def test_connecting_stores_only_the_gateways_ciphertext(platform, people):
    iid = await _installed(platform, people)
    response = await _connect(platform, people["bob"], iid)
    assert response.status_code == 204, response.text
    bob_id = UUID(sql(platform, "SELECT id FROM users WHERE username = 'bob'")[0]["id"].hex)
    assert platform.app.state.gateway.encrypted == [(UUID(iid), bob_id, SECRET)]
    (row,) = sql(platform, "SELECT ciphertext, status FROM integration_credentials")
    assert bytes(row["ciphertext"]) == b"sealed:" + str(bob_id).encode()
    assert (await _mine(platform, people["bob"]))["home"]["status"] == "connected"


async def test_no_api_returns_credential_material(platform, people):
    iid = await _installed(platform, people)
    await _connect(platform, people["bob"], iid)
    sealed = [b"sealed:", base64.b64encode(b"sealed:")[:8]]
    for path, headers in [
        (f"{API}/integrations", people["bob"]),
        (f"{API}/integrations", people["bob_agent"]),
        (ADMIN, people["admin"]),
        (f"{API}/audit", people["admin"]),
    ]:
        response = await platform.client.get(path, headers=headers)
        assert response.status_code == 200, (path, response.text)
        assert SECRET not in response.text, path
        for s in sealed:
            assert s.decode() not in response.text, path
    admin = (await platform.client.get(ADMIN, headers=people["admin"])).json()
    (connection,) = admin["integrations"][0]["connections"]
    assert set(connection) == {"user_id", "username", "display_name", "status", "updated_at"}


async def test_the_grant_carries_the_users_own_credential(platform, people):
    iid = await _installed(platform, people)
    await _admin_tools(platform, people, iid, {"name": "turn_on", "enabled": True})
    await _connect(platform, people["bob"], iid)

    response = await _grant(platform, people["bob_agent"])
    assert response.status_code == 200, response.text
    got = response.json()
    assert got["status"] == "connected"
    assert base64.b64decode(got["credential"]["ciphertext"]).startswith(b"sealed:")
    assert got["credential"]["user_id"] == got["user_id"]
    assert got["integration"]["url"] == "https://ha.example.com/mcp"
    assert got["tools"] == {
        "get_state": {"available": False, "permission": "ask", "can_always_allow": True},
        "turn_on": {"available": True, "permission": "ask", "can_always_allow": True},
    }

    carol = (await _grant(platform, people["carol_agent"])).json()
    assert (carol["status"], carol["credential"]) == ("not_connected", None)
    assert (await _grant(platform, people["bob_agent"], slug="nope")).status_code == 404


async def test_a_shared_credential_serves_everyone(platform, people):
    iid = await _installed(platform, people, credential_model="shared")
    assert (await _connect(platform, people["bob"], iid)).json()["detail"] == (
        "wrong_credential_model"
    )
    response = await platform.client.put(
        f"{ADMIN}/{iid}/shared-credential", json={"secret": SECRET}, headers=people["admin"]
    )
    assert response.status_code == 204, response.text
    assert platform.app.state.gateway.encrypted[-1] == (UUID(iid), None, SECRET)
    for agent in ("bob_agent", "carol_agent"):
        got = (await _grant(platform, people[agent])).json()
        assert (got["status"], got["credential"]["user_id"]) == ("connected", None)
    admin = (await platform.client.get(ADMIN, headers=people["admin"])).json()
    assert admin["integrations"][0]["shared_credential"] == "connected"
    assert admin["integrations"][0]["connections"] == []

    response = await platform.client.delete(
        f"{ADMIN}/{iid}/shared-credential", headers=people["admin"]
    )
    assert response.status_code == 204
    assert (await _grant(platform, people["bob_agent"])).json()["status"] == "not_connected"


async def test_refresh_discovers_with_the_admins_own_credential(platform, people):
    iid = (await _install(platform, people))["id"]
    await _connect(platform, people["admin"], iid)
    await _refresh(platform, people, iid)
    _, credential = platform.app.state.gateway.discovered[-1]
    assert base64.b64decode(credential["ciphertext"]).startswith(b"sealed:")


async def test_no_auth_needs_no_connection(platform, people):
    iid = await _installed(platform, people, credential_model="none", auth_method="none")
    assert (await _mine(platform, people["bob"]))["home"]["status"] == "ready"
    got = (await _grant(platform, people["bob_agent"])).json()
    assert (got["status"], got["credential"]) == ("ready", None)
    assert (await _connect(platform, people["bob"], iid)).status_code == 422


async def test_disconnecting(platform, people):
    iid = await _installed(platform, people)
    await _connect(platform, people["bob"], iid)
    response = await platform.client.delete(
        f"{API}/integrations/{iid}/connection", headers=people["bob"]
    )
    assert response.status_code == 204
    assert sql(platform, "SELECT 1 FROM integration_credentials") == []

    await _connect(platform, people["bob"], iid)
    bob_id = sql(platform, "SELECT id FROM users WHERE username = 'bob'")[0]["id"]
    response = await platform.client.delete(
        f"{ADMIN}/{iid}/connections/{bob_id}", headers=people["admin"]
    )
    assert response.status_code == 204
    assert sql(platform, "SELECT 1 FROM integration_credentials") == []


async def test_a_rejected_credential_needs_reconnecting(platform, people):
    iid = await _installed(platform, people)
    await _connect(platform, people["bob"], iid)
    bob_id = sql(platform, "SELECT id FROM users WHERE username = 'bob'")[0]["id"]
    response = await platform.client.post(
        f"/internal/integrations/{iid}/credential-status",
        json={"user_id": str(bob_id), "status": "needs_reconnecting"},
        headers=bearer(GATEWAY_TOKEN),
    )
    assert response.status_code == 204, response.text
    assert (await _mine(platform, people["bob"]))["home"]["status"] == "needs_reconnecting"
    await _connect(platform, people["bob"], iid)
    assert (await _mine(platform, people["bob"]))["home"]["status"] == "connected"


# --- wiping credentials ----------------------------------------------------------------


async def test_uninstall_wipes_credentials_and_tool_settings(platform, people):
    iid = await _installed(platform, people)
    other = await _installed(platform, people, slug="other", name="Other")
    for i in (iid, other):
        await _admin_tools(platform, people, i, {"name": "turn_on", "enabled": True})
        await platform.client.patch(
            f"{API}/integrations/{i}/tools",
            json={"changes": [{"name": "turn_on", "permission": "allow"}]},
            headers=people["bob"],
        )
        await _connect(platform, people["bob"], i)

    response = await platform.client.delete(f"{ADMIN}/{iid}", headers=people["admin"])
    assert response.status_code == 204
    for table in ("agent_tool_admin_settings", "agent_tool_user_settings"):
        keys = [r["tool"] for r in sql(platform, f"SELECT tool FROM {table}")]
        assert not [k for k in keys if iid in k], table
        assert [k for k in keys if other in k], table
    rows = sql(platform, "SELECT integration_id FROM integration_credentials")
    assert [str(r["integration_id"]) for r in rows] == [other]
    assert sql(platform, "SELECT 1 FROM integration_tools WHERE integration_id = %s", (iid,)) == []


async def test_deleting_a_user_deletes_their_connections(platform, people):
    iid = await _installed(platform, people)
    await _connect(platform, people["bob"], iid)
    sql(
        platform,
        "DELETE FROM spaces s USING users u WHERE u.id = s.owner_user_id AND u.username = 'bob'",
    )
    sql(platform, "DELETE FROM users WHERE username = 'bob'")
    assert sql(platform, "SELECT 1 FROM integration_credentials") == []


async def test_a_new_url_or_credential_model_wipes_credentials(platform, people):
    iid = await _installed(platform, people)
    await _connect(platform, people["bob"], iid)
    response = await platform.client.patch(
        f"{ADMIN}/{iid}", json={"name": "Home Assistant"}, headers=people["admin"]
    )
    assert response.status_code == 200, response.text
    assert len(sql(platform, "SELECT 1 FROM integration_credentials")) == 1

    response = await platform.client.patch(
        f"{ADMIN}/{iid}",
        json={"url": "https://elsewhere.example.com/mcp"},
        headers=people["admin"],
    )
    assert response.status_code == 200, response.text
    assert sql(platform, "SELECT 1 FROM integration_credentials") == []

    await _connect(platform, people["bob"], iid)
    response = await platform.client.patch(
        f"{ADMIN}/{iid}", json={"credential_model": "shared"}, headers=people["admin"]
    )
    assert response.status_code == 200, response.text
    assert sql(platform, "SELECT 1 FROM integration_credentials") == []


# --- one availability check ------------------------------------------------------------


async def test_one_availability_check_gates_every_path(platform, people, monkeypatch):
    iid = await _installed(platform, people)
    monkeypatch.setattr(integrations, "usable_by", lambda integration, user_id: False)
    assert await _mine(platform, people["bob"]) == {}
    assert (await _grant(platform, people["bob_agent"])).status_code == 404
    assert (await _connect(platform, people["bob"], iid)).status_code == 404
    response = await platform.client.patch(
        f"{API}/integrations/{iid}/tools",
        json={"changes": [{"name": "turn_on", "permission": "allow"}]},
        headers=people["bob"],
    )
    assert response.status_code == 404


async def test_audit_records_installs_and_connections_without_secrets(platform, people):
    iid = await _installed(platform, people)
    await _connect(platform, people["bob"], iid)
    rows = sql(platform, "SELECT kind, summary, detail FROM audit_events ORDER BY id")
    kinds = [r["kind"] for r in rows]
    assert {"integrations.installed", "integrations.discovered", "integrations.connected"} <= set(
        kinds
    )
    assert SECRET not in json.dumps([dict(r) for r in rows], default=str)


# --- approval markers for calls (M18-04) -----------------------------------------------


def _deleg(headers) -> str:
    return headers["Authorization"].split()[1]


async def _mint(platform, agent, tool="turn_on", arguments=None, token=AGENT_TOKEN):
    return await platform.client.post(
        "/internal/integration-approvals",
        json={
            "delegation": _deleg(agent),
            "integration": "home",
            "tool": tool,
            "arguments": arguments
            if arguments is not None
            else {"entity": "light.kitchen", "on": True},
        },
        headers=bearer(token),
    )


async def _spend(platform, agent, marker, tool="turn_on", arguments=None, token=GATEWAY_TOKEN):
    return await platform.client.post(
        "/internal/integration-approvals/consume",
        json={
            "delegation": _deleg(agent),
            "token": marker,
            "integration": "home",
            "tool": tool,
            "arguments": arguments
            if arguments is not None
            else {"on": True, "entity": "light.kitchen"},
        },
        headers=bearer(token),
    )


async def test_a_call_marker_runs_its_exact_call_once(platform, people):
    await _installed(platform, people)
    agent = people["bob_agent"]
    response = await _mint(platform, agent)
    assert response.status_code == 200, response.text
    marker = response.json()["token"]
    assert (await _spend(platform, agent, marker)).status_code == 204  # key order doesn't matter
    response = await _spend(platform, agent, marker)
    assert (response.status_code, response.json()["detail"]) == (403, "hitl_approval_required")


@pytest.mark.parametrize(
    "change",
    [
        {"arguments": {"entity": "light.kitchen", "on": False}},
        {"arguments": {"entity": "light.kitchen", "on": True, "extra": 1}},
        {"tool": "get_state"},
    ],
)
async def test_a_call_marker_is_bound_to_the_tool_and_arguments(platform, people, change):
    await _installed(platform, people)
    agent = people["bob_agent"]
    marker = (await _mint(platform, agent)).json()["token"]
    assert (await _spend(platform, agent, marker, **change)).status_code == 403
    # A mismatched try spends it.
    assert (await _spend(platform, agent, marker)).status_code == 403


async def test_a_call_marker_is_bound_to_the_user(platform, people):
    await _installed(platform, people)
    marker = (await _mint(platform, people["bob_agent"])).json()["token"]
    assert (await _spend(platform, people["carol_agent"], marker)).status_code == 403


async def test_call_markers_need_the_right_services(platform, people):
    await _installed(platform, people)
    agent = people["bob_agent"]
    assert (await _mint(platform, agent, token=GATEWAY_TOKEN)).status_code == 401
    marker = (await _mint(platform, agent)).json()["token"]
    assert (await _spend(platform, agent, marker, token=AGENT_TOKEN)).status_code == 401
    assert (await _mint(platform, agent, tool="no_such_tool")).status_code == 404


async def test_the_gateway_records_calls_in_the_audit_log(platform, people):
    iid = await _installed(platform, people)
    response = await platform.client.post(
        "/internal/integration-audit",
        json={
            "delegation": _deleg(people["bob_agent"]),
            "integration": "home",
            "action": "call",
            "target": "turn_on",
            "arguments": {"entity": "light.kitchen"},
            "context": "chat",
            "approval": "approved",
            "status": "error",
            "error_code": "timeout",
        },
        headers=bearer(GATEWAY_TOKEN),
    )
    assert response.status_code == 204, response.text
    (row,) = sql(
        platform,
        "SELECT actor_kind, summary, target_id, detail FROM audit_events "
        "WHERE kind = 'integrations.call'",
    )
    assert row["actor_kind"] == "agent"
    assert row["summary"] == "Called turn_on on Home (failed: timeout)"
    assert str(row["target_id"]) == iid
    assert row["detail"]["approval"] == "approved"
    assert "delegation" not in row["detail"]

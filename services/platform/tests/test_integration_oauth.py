"""OAuth sign-in for integrations (docs/INTEGRATIONS.md §5.1): start, return page, client page,
the gateway's write-backs, egress rules and revoking."""

import base64
from pathlib import Path
from uuid import UUID

import pytest

from app.core import integration_oauth
from app.core.config import Settings
from app.core.errors import InvalidInput, Unavailable
from app.main import create_app
from tests.conftest import Platform, make_settings, running
from tests.helpers import bearer, bootstrap_admin, create_user, identity, login, sql, step_up
from tests.test_integrations import (
    ADMIN,
    AGENT_TOKEN,
    API,
    EGRESS_TOKEN,
    GATEWAY_TOKEN,
    FakeGateway,
    _install,
    _mine,
)

LAN = "192.168.0.108"
RETURN = f"http://{LAN}/api/platform/integrations/oauth/return"
OAUTH = {
    "summary": {
        "issuer": "https://agent.example/mcp/trading",
        "sign_in_host": "example.com",
        "token_host": "api.example.com",
        "registration": "dynamic",
        "revocable": False,
    },
    "endpoints": [
        "https://agent.example/oauth/register",
        "https://api.example.com/oauth2/token/",
        "https://agent.example/.well-known/oauth-authorization-server/mcp/trading",
    ],
}


class OAuthGateway(FakeGateway):
    def __init__(self) -> None:
        super().__init__()
        self.starts: list[dict] = []
        self.revoked: list[tuple] = []
        self.new_client: dict | None = {"kind": "dynamic", "client_id": "client-1"}
        self.finished: dict | None = None
        self.finish_fails: Exception | None = None

    async def oauth_start(self, integration, owner, started_by, return_url, client):
        self.starts.append(
            {
                "integration": integration,
                "owner": owner,
                "started_by": started_by,
                "return_url": return_url,
                "client": client,
            }
        )
        new = None
        if client is None and self.new_client is not None:
            new = {
                **self.new_client,
                "redirect_uri": return_url,
                "issuer": OAUTH["summary"]["issuer"],
            }
        self.finished = {
            "integration_id": str(integration["id"]),
            "owner": str(owner) if owner else None,
            "started_by": started_by,
            "ciphertext": base64.b64encode(b"sealed-oauth:" + str(owner).encode()).decode(),
            "checked": True,
            "problem": None,
        }
        return {
            "authorize_url": "https://example.com/oauth?state=s1",
            "client": new,
            "oauth": OAUTH["summary"],
        }

    async def oauth_finish(self, state, code, iss, error):
        if self.finish_fails is not None:
            raise self.finish_fails
        if error:
            raise InvalidInput("oauth_denied")
        if state != "s1" or self.finished is None:
            raise InvalidInput("oauth_state_invalid")
        done, self.finished = self.finished, None
        return done

    async def oauth_revoke(self, integration, credential):
        self.revoked.append((integration["id"], credential["user_id"]))
        return {"revoked": True}


@pytest.fixture
async def platform(pg_database, tmp_path: Path):
    app = create_app(
        make_settings(
            pg_database,
            tmp_path,
            platform_gateway_token=GATEWAY_TOKEN,
            platform_egress_token=EGRESS_TOKEN,
            platform_agent_token=AGENT_TOKEN,
            homeai_lan_addresses=LAN,
        )
    )
    async with running(app) as client:
        app.state.gateway = OAuthGateway()
        yield Platform(app, client, pg_database, tmp_path)


@pytest.fixture
async def people(platform):
    admin_token = await bootstrap_admin(platform)
    await step_up(platform, admin_token)
    await create_user(platform, "bob")
    await create_user(platform, "carol")
    return {
        "admin": {**await identity(platform, admin_token), "Host": LAN},
        "bob": {**await identity(platform, await login(platform, "bob")), "Host": LAN},
        "carol": {**await identity(platform, await login(platform, "carol")), "Host": LAN},
    }


async def _oauth_installed(platform, people, **overrides) -> str:
    body = {
        "slug": "trading",
        "name": "Trading",
        "url": "https://agent.example/mcp/trading",
        "credential_model": "per_user",
        "auth_method": "oauth",
        **overrides,
    }
    iid = (await _install(platform, people, **body))["id"]
    response = await platform.client.put(
        f"/internal/integrations/{iid}/oauth", json=OAUTH, headers=bearer(GATEWAY_TOKEN)
    )
    assert response.status_code == 204, response.text
    return iid


async def _start(platform, headers, iid, admin=False):
    path = f"{ADMIN}/{iid}/oauth/start" if admin else f"{API}/integrations/{iid}/oauth/start"
    return await platform.client.post(path, headers=headers)


async def _return(platform, **query):
    return await platform.client.get(
        f"{API}/integrations/oauth/return", params=query, headers={"Host": LAN}
    )


def _gateway(platform) -> OAuthGateway:
    return platform.app.state.gateway


# --- signing in ------------------------------------------------------------------------


async def test_sign_in_then_the_return_page_stores_it(platform, people):
    iid = await _oauth_installed(platform, people)
    response = await _start(platform, people["bob"], iid)
    assert response.status_code == 200, response.text
    assert response.json() == {"authorize_url": "https://example.com/oauth?state=s1"}
    start = _gateway(platform).starts[-1]
    bob_id = start["started_by"]["user_id"]
    assert start["return_url"] == RETURN
    assert str(start["owner"]) == bob_id
    assert start["client"] is None

    page = await _return(platform, state="s1", code="c1", iss=OAUTH["summary"]["issuer"])
    assert page.status_code == 200, page.text
    assert "Connected to Trading" in page.text
    assert f"homeai://settings/integrations/{iid}" in page.text
    assert page.headers["cache-control"] == "no-store"
    rows = sql(platform, "SELECT user_id, ciphertext, set_by, status FROM integration_credentials")
    assert [(str(r["user_id"]), bytes(r["ciphertext"]), str(r["set_by"])) for r in rows] == [
        (bob_id, f"sealed-oauth:{bob_id}".encode(), bob_id)
    ]
    assert (await _mine(platform, people["bob"]))["trading"]["status"] == "connected"
    assert (await _mine(platform, people["carol"]))["trading"]["status"] == "not_connected"
    events = sql(
        platform,
        "SELECT actor_user_id, actor_name FROM audit_events WHERE kind = 'integrations.connected'",
    )
    assert [str(e["actor_user_id"]) for e in events] == [bob_id]


async def test_a_registered_client_is_kept_and_reused(platform, people):
    iid = await _oauth_installed(platform, people)
    await _start(platform, people["bob"], iid)
    clients = sql(platform, "SELECT redirect_uri, client_id, kind FROM integration_oauth_clients")
    assert clients == [{"redirect_uri": RETURN, "client_id": "client-1", "kind": "dynamic"}]
    await _start(platform, people["carol"], iid)
    assert _gateway(platform).starts[-1]["client"]["client_id"] == "client-1"


async def test_sign_in_needs_one_of_this_servers_addresses(platform, people):
    iid = await _oauth_installed(platform, people)
    for host in ("evil.example", "192.168.0.99", ""):
        response = await _start(platform, {**people["bob"], "Host": host}, iid)
        assert response.status_code == 422
        assert response.json()["detail"] == "unknown_address"
    response = await _start(platform, {**people["bob"], "Host": "10.13.13.1:8080"}, iid)
    assert response.status_code == 200
    assert _gateway(platform).starts[-1]["return_url"] == (
        "http://10.13.13.1:8080/api/platform/integrations/oauth/return"
    )


async def test_sign_in_is_for_oauth_and_the_right_model(platform, people):
    token_iid = (await _install(platform, people))["id"]
    assert (await _start(platform, people["bob"], token_iid)).json()["detail"] == (
        "auth_method_mismatch"
    )
    shared = await _oauth_installed(platform, people, slug="shared", credential_model="shared")
    assert (await _start(platform, people["bob"], shared)).json()["detail"] == (
        "wrong_credential_model"
    )
    per_user = await _oauth_installed(platform, people, slug="mine")
    assert (await _start(platform, people["admin"], per_user, admin=True)).json()["detail"] == (
        "wrong_credential_model"
    )


async def test_shared_sign_in_is_the_admins(platform, people):
    iid = await _oauth_installed(platform, people, credential_model="shared")
    assert (await _start(platform, people["bob"], iid, admin=True)).status_code == 403
    response = await _start(platform, people["admin"], iid, admin=True)
    assert response.status_code == 200, response.text
    assert _gateway(platform).starts[-1]["owner"] is None
    page = await _return(platform, state="s1", code="c1")
    assert page.status_code == 200
    assert f"settings/integrations/manage/{iid}" in page.text
    rows = sql(platform, "SELECT user_id FROM integration_credentials")
    assert rows == [{"user_id": None}]


@pytest.mark.parametrize(
    ("query", "words"),
    [
        ({"state": "s1", "error": "access_denied"}, "cancelled or refused"),
        ({"state": "other", "code": "c1"}, "already used or has expired"),
        ({}, "already used or has expired"),
    ],
)
async def test_a_failed_sign_in_stores_nothing(platform, people, query, words):
    iid = await _oauth_installed(platform, people)
    await _start(platform, people["bob"], iid)
    page = await _return(platform, **query)
    assert page.status_code == 400
    assert words in page.text
    assert sql(platform, "SELECT 1 FROM integration_credentials") == []


async def test_the_return_page_escapes_and_needs_no_session(platform, people):
    iid = await _oauth_installed(platform, people, name="<b>Trading</b>")
    await _start(platform, people["bob"], iid)
    page = await _return(platform, state="s1", code="c1")
    assert page.status_code == 200
    assert "&lt;b&gt;Trading&lt;/b&gt;" in page.text
    assert "<b>" not in page.text
    assert "default-src 'none'" in page.headers["content-security-policy"]


async def test_a_sign_in_for_someone_else_is_not_stored(platform, people):
    iid = await _oauth_installed(platform, people)
    await _start(platform, people["bob"], iid)
    gateway = _gateway(platform)
    gateway.finished["started_by"] = {**gateway.finished["started_by"], "user_id": str(UUID(int=7))}
    page = await _return(platform, state="s1", code="c1")
    assert page.status_code == 400
    assert sql(platform, "SELECT 1 FROM integration_credentials") == []


async def test_a_sign_in_after_the_server_stopped_using_oauth_is_not_stored(platform, people):
    iid = await _oauth_installed(platform, people)
    await _start(platform, people["bob"], iid)
    response = await platform.client.patch(
        f"{ADMIN}/{iid}", json={"auth_method": "token"}, headers=people["admin"]
    )
    assert response.status_code == 200, response.text
    page = await _return(platform, state="s1", code="c1")
    assert page.status_code == 400
    assert sql(platform, "SELECT 1 FROM integration_credentials") == []


# --- the client ID page ------------------------------------------------------------------


async def test_the_client_page_names_the_return_address(platform):
    page = await platform.client.get(
        f"{API}/integrations/oauth/client", headers={"Host": f"{LAN}:80"}
    )
    assert page.status_code == 200
    assert f'<link rel="redirect_uri" href="{RETURN}">' in page.text
    doc = await platform.client.get(
        f"{API}/integrations/oauth/client",
        headers={"Host": LAN, "Accept": "application/json"},
    )
    assert doc.json()["client_id"] == f"http://{LAN}/api/platform/integrations/oauth/client"
    assert doc.json()["redirect_uris"] == [RETURN]
    assert doc.json()["token_endpoint_auth_method"] == "none"
    other = await platform.client.get(
        f"{API}/integrations/oauth/client", headers={"Host": "evil.example"}
    )
    assert other.status_code == 404


def test_return_addresses():
    s = Settings(homeai_lan_addresses=f"{LAN},fd00::8", homeai_domain="home.example.org")
    assert integration_oauth.return_url(s, "home.example.org", True) == (
        "https://home.example.org/api/platform/integrations/oauth/return"
    )
    assert integration_oauth.return_url(s, "[fd00::8]:8443", True) == (
        "https://[fd00::8]:8443/api/platform/integrations/oauth/return"
    )
    assert integration_oauth.return_url(s, f"{LAN}, evil.example", False) == RETURN
    with pytest.raises(InvalidInput):
        integration_oauth.return_url(s, "evil.example", True)


# --- the gateway's write-backs ----------------------------------------------------------


async def test_oauth_endpoints_join_the_egress_rules(platform, people):
    await _oauth_installed(platform, people)
    response = await platform.client.get("/internal/egress-rules", headers=bearer(EGRESS_TOKEN))
    rules = {(r["host"], r["port"]): r for r in response.json()["rules"]}
    assert set(rules) == {("agent.example", 443), ("api.example.com", 443)}
    assert rules[("api.example.com", 443)] == {
        "integration": "trading",
        "scheme": "https",
        "host": "api.example.com",
        "port": 443,
        "private_address": False,
        "cert_fingerprint": None,
    }
    mine = await _mine(platform, people["bob"])
    assert mine["trading"]["oauth"]["sign_in_host"] == "example.com"
    assert mine["trading"]["oauth"]["revocable"] is False


async def test_discovery_so_far_adds_to_the_kept_endpoints(platform, people):
    iid = await _oauth_installed(platform, people)
    interim = {"summary": None, "endpoints": ["https://meta.example/.well-known/x"]}
    response = await platform.client.put(
        f"/internal/integrations/{iid}/oauth", json=interim, headers=bearer(GATEWAY_TOKEN)
    )
    assert response.status_code == 204
    response = await platform.client.get("/internal/egress-rules", headers=bearer(EGRESS_TOKEN))
    hosts = {r["host"] for r in response.json()["rules"]}
    assert hosts == {"agent.example", "api.example.com", "meta.example"}
    assert (await _mine(platform, people["bob"]))["trading"]["oauth"]["token_host"] == (
        "api.example.com"
    )
    final = {**OAUTH, "endpoints": ["https://api.example.com/oauth2/token/"]}
    await platform.client.put(
        f"/internal/integrations/{iid}/oauth", json=final, headers=bearer(GATEWAY_TOKEN)
    )
    response = await platform.client.get("/internal/egress-rules", headers=bearer(EGRESS_TOKEN))
    assert {r["host"] for r in response.json()["rules"]} == {"agent.example", "api.example.com"}


async def test_a_token_integrations_oauth_endpoints_are_not_reached(platform, people):
    iid = (await _install(platform, people))["id"]
    response = await platform.client.put(
        f"/internal/integrations/{iid}/oauth", json=OAUTH, headers=bearer(GATEWAY_TOKEN)
    )
    assert response.status_code == 204
    response = await platform.client.get("/internal/egress-rules", headers=bearer(EGRESS_TOKEN))
    assert [r["host"] for r in response.json()["rules"]] == ["ha.example.com"]


async def test_a_private_endpoint_is_refused_unless_on_the_servers_own_address(platform, people):
    iid = await _oauth_installed(
        platform,
        people,
        url="http://192.168.0.50:8123/api/mcp",
        private_address=True,
    )
    own = {"summary": OAUTH["summary"], "endpoints": ["http://192.168.0.50:8123/auth/token"]}
    response = await platform.client.put(
        f"/internal/integrations/{iid}/oauth", json=own, headers=bearer(GATEWAY_TOKEN)
    )
    assert response.status_code == 204
    for url in (
        "http://192.168.0.50:9000/auth/token",
        "https://192.168.0.1/token",
        "https://localhost/token",
        "http://tokens.example/token",
    ):
        response = await platform.client.put(
            f"/internal/integrations/{iid}/oauth",
            json={"summary": None, "endpoints": [url]},
            headers=bearer(GATEWAY_TOKEN),
        )
        assert response.status_code == 422, url
        assert response.json()["detail"] == "invalid_endpoint"
    response = await platform.client.get("/internal/egress-rules", headers=bearer(EGRESS_TOKEN))
    assert [(r["host"], r["port"]) for r in response.json()["rules"]] == [("192.168.0.50", 8123)]
    too_many = {"summary": None, "endpoints": [f"https://a{i}.example/t" for i in range(9)]}
    response = await platform.client.put(
        f"/internal/integrations/{iid}/oauth", json=too_many, headers=bearer(GATEWAY_TOKEN)
    )
    assert response.status_code == 422


async def test_a_refreshed_credential_replaces_the_stored_one(platform, people):
    iid = await _oauth_installed(platform, people)
    await _start(platform, people["bob"], iid)
    bob_id = _gateway(platform).starts[-1]["started_by"]["user_id"]
    await _return(platform, state="s1", code="c1")
    sql(platform, "UPDATE integration_credentials SET status = 'needs_reconnecting'")
    body = {"user_id": bob_id, "ciphertext": base64.b64encode(b"refreshed").decode()}
    response = await platform.client.put(
        f"/internal/integrations/{iid}/credential", json=body, headers=bearer(GATEWAY_TOKEN)
    )
    assert response.status_code == 204, response.text
    rows = sql(platform, "SELECT ciphertext, status FROM integration_credentials")
    assert [(bytes(r["ciphertext"]), r["status"]) for r in rows] == [(b"refreshed", "connected")]
    carol = {**body, "user_id": str(UUID(int=9))}
    response = await platform.client.put(
        f"/internal/integrations/{iid}/credential", json=carol, headers=bearer(GATEWAY_TOKEN)
    )
    assert response.status_code == 404
    for token in (AGENT_TOKEN, EGRESS_TOKEN):
        response = await platform.client.put(
            f"/internal/integrations/{iid}/credential", json=body, headers=bearer(token)
        )
        assert response.status_code == 401


async def test_a_new_address_forgets_the_oauth_metadata_and_clients(platform, people):
    iid = await _oauth_installed(platform, people)
    await _start(platform, people["bob"], iid)
    response = await platform.client.patch(
        f"{ADMIN}/{iid}", json={"url": "https://other.example/mcp"}, headers=people["admin"]
    )
    assert response.status_code == 200
    assert response.json()["oauth"] is None
    assert sql(platform, "SELECT 1 FROM integration_oauth_clients") == []
    response = await platform.client.get("/internal/egress-rules", headers=bearer(EGRESS_TOKEN))
    assert [r["host"] for r in response.json()["rules"]] == ["other.example"]


# --- disconnecting ------------------------------------------------------------------------


async def test_disconnecting_revokes_first(platform, people):
    iid = await _oauth_installed(platform, people)
    await _start(platform, people["bob"], iid)
    await _return(platform, state="s1", code="c1")
    response = await platform.client.delete(
        f"{API}/integrations/{iid}/connection", headers=people["bob"]
    )
    assert response.status_code == 204
    assert len(_gateway(platform).revoked) == 1
    assert sql(platform, "SELECT 1 FROM integration_credentials") == []
    response = await platform.client.delete(
        f"{API}/integrations/{iid}/connection", headers=people["bob"]
    )
    assert response.status_code == 204
    assert len(_gateway(platform).revoked) == 1


async def test_a_failed_revoke_still_disconnects(platform, people):
    iid = await _oauth_installed(platform, people, credential_model="shared")
    await _start(platform, people["admin"], iid, admin=True)
    await _return(platform, state="s1", code="c1")

    async def refuse(integration, credential):
        raise Unavailable("gateway_unavailable")

    _gateway(platform).oauth_revoke = refuse
    response = await platform.client.delete(
        f"{ADMIN}/{iid}/shared-credential", headers=people["admin"]
    )
    assert response.status_code == 204
    assert sql(platform, "SELECT 1 FROM integration_credentials") == []


async def test_a_token_disconnect_doesnt_revoke(platform, people):
    iid = (await _install(platform, people))["id"]
    response = await platform.client.put(
        f"{API}/integrations/{iid}/connection", json={"secret": "tok"}, headers=people["bob"]
    )
    assert response.status_code == 200, response.text
    await platform.client.delete(f"{API}/integrations/{iid}/connection", headers=people["bob"])
    assert _gateway(platform).revoked == []


async def test_the_check_passes_on_the_oauth_summary(platform, people):
    iid = await _oauth_installed(platform, people)
    _gateway(platform).check_result = {
        "reachable": True,
        "auth": "oauth",
        "oauth": OAUTH["summary"],
    }
    response = await platform.client.post(f"{ADMIN}/{iid}/check", headers=people["admin"])
    assert response.status_code == 200, response.text
    assert response.json()["oauth"]["token_host"] == "api.example.com"
    _gateway(platform).check_result = {
        "reachable": True,
        "auth": "oauth",
        "oauth": {"problem": "oauth_unsupported", "detail": "No PKCE."},
    }
    response = await platform.client.post(f"{ADMIN}/{iid}/check", headers=people["admin"])
    assert response.json()["oauth"]["problem"] == "oauth_unsupported"

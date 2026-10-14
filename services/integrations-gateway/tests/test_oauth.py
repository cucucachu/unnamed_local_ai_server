"""OAuth sign-in, refresh and revocation (docs/INTEGRATIONS.md §5.1), against the fake provider."""

from __future__ import annotations

import asyncio
import json
from urllib.parse import parse_qs, urlsplit, urlunsplit

import httpx2
import pytest

from app.oauth import CLIENT_ID_PATH, OAuth, OAuthError, OAuthHttp, endpoint_allowed
from app.service import Gateway
from tests.conftest import DELEGATION, INTEGRATION, USER, FakePlatform
from tests.fake_oauth import FakeProvider

RETURN = "http://192.168.0.108/api/platform/integrations/oauth/return"
ALICE = {"user_id": str(USER), "name": "Alice"}


@pytest.fixture(params=["robinhood", "ha"])
def provider(request: pytest.FixtureRequest) -> FakeProvider:
    return FakeProvider(mode=request.param)


@pytest.fixture
def oauth_platform(platform: FakePlatform, provider: FakeProvider) -> FakePlatform:
    platform.url = f"{provider.mcp}/mcp"
    platform.auth_method = "oauth"
    platform.ciphertext = ""

    def allowed(host: str) -> bool:
        # The egress proxy: the MCP host, plus what the platform has stored.
        hosts = {urlsplit(provider.mcp).netloc}
        hosts |= {urlsplit(u).netloc for u in platform.oauth_endpoints}
        return host in hosts

    provider.allowed = allowed
    return platform


@pytest.fixture
def oauth(oauth_platform: FakePlatform, provider: FakeProvider, monkeypatch) -> OAuth:
    monkeypatch.setattr("app.oauth.RULES_MISS_WAIT_S", 0)
    http = OAuthHttp(None, "", 5.0, transport=httpx2.ASGITransport(app=provider))
    return OAuth(http, oauth_platform)


@pytest.fixture
def gw(settings, key, oauth_platform, connector, oauth, provider) -> Gateway:
    from app.handles import InputHandles

    connector.accept = lambda h: (
        h.get("Authorization", "").removeprefix("Bearer ") in (provider.access)
    )
    return Gateway(settings, key, oauth_platform, connector, InputHandles(60), oauth)


def _integration(platform: FakePlatform) -> dict:
    return {
        "id": str(INTEGRATION),
        "slug": "home",
        "url": platform.url,
        "auth_method": "oauth",
        "credential_model": "per_user",
        "cert_fingerprint": None,
        "private_address": False,
        "token_header": None,
        "protocol_version": None,
    }


def _query(url: str) -> dict[str, str]:
    return {k: v[0] for k, v in parse_qs(urlsplit(url).query).items()}


async def _sign_in(gw: Gateway, platform: FakePlatform, provider: FakeProvider, **answer) -> dict:
    started = await gw.oauth_start(_integration(platform), USER, ALICE, RETURN, None)
    back = _query(provider.authorize(started["authorize_url"], **answer))
    return await gw.oauth_finish(
        back["state"], back.get("code"), back.get("iss"), back.get("error")
    )


async def test_sign_in_then_use(gw, oauth_platform, provider, connector):
    started = await gw.oauth_start(_integration(oauth_platform), USER, ALICE, RETURN, None)
    sent = _query(started["authorize_url"])
    assert sent["redirect_uri"] == RETURN and sent["code_challenge_method"] == "S256"
    assert sent["resource"] == (
        f"{provider.mcp}/mcp" if provider.mode == "robinhood" else provider.mcp
    )
    client = started["client"]
    if provider.mode == "robinhood":
        assert client["kind"] == "dynamic" and client["client_id"] in provider.clients
        assert started["oauth"]["token_host"] == "tokens.example"
        assert "https://tokens.example/oauth2/token/" in oauth_platform.oauth_endpoints
    else:
        assert client["client_id"] == "http://192.168.0.108" + CLIENT_ID_PATH
        assert started["oauth"]["revocable"] is True

    back = _query(provider.authorize(started["authorize_url"]))
    done = await gw.oauth_finish(back["state"], back["code"], back.get("iss"), None)
    assert (done["owner"], done["started_by"], done["checked"]) == (USER, ALICE, True)
    assert "at-" not in json.dumps(done, default=str)  # sealed, never in the clear
    exchange = provider.token_requests[-1]
    assert exchange["grant_type"] == "authorization_code" and exchange["code_verifier"]

    oauth_platform.ciphertext = done["ciphertext"]
    opened = await gw.open(DELEGATION, "home", "chat")
    assert opened["ok"], opened
    assert connector.headers[-1]["Authorization"].removeprefix("Bearer ") in provider.access


async def test_a_state_works_once_and_expires(gw, oauth, oauth_platform, provider):
    started = await gw.oauth_start(_integration(oauth_platform), USER, ALICE, RETURN, None)
    back = _query(provider.authorize(started["authorize_url"]))
    await gw.oauth_finish(back["state"], back["code"], back.get("iss"), None)
    with pytest.raises(OAuthError) as again:
        await gw.oauth_finish(back["state"], back["code"], back.get("iss"), None)
    assert again.value.code == "oauth_state_invalid"

    oauth.flow_ttl_s = 0
    started = await gw.oauth_start(_integration(oauth_platform), USER, ALICE, RETURN, None)
    back = _query(provider.authorize(started["authorize_url"]))
    with pytest.raises(OAuthError) as late:
        await gw.oauth_finish(back["state"], back["code"], back.get("iss"), None)
    assert late.value.code == "oauth_state_invalid"


async def test_a_refusal_or_another_issuer_stores_nothing(gw, oauth_platform, provider):
    with pytest.raises(OAuthError) as denied:
        await _sign_in(gw, oauth_platform, provider, approve=False)
    assert denied.value.code == "oauth_denied"

    started = await gw.oauth_start(_integration(oauth_platform), USER, ALICE, RETURN, None)
    back = _query(provider.authorize(started["authorize_url"]))
    with pytest.raises(OAuthError) as mixed:
        await gw.oauth_finish(back["state"], back["code"], "https://evil.example", None)
    assert mixed.value.code == "oauth_issuer_mismatch"


async def test_a_registered_client_is_reused(gw, oauth_platform, provider):
    first = await gw.oauth_start(_integration(oauth_platform), USER, ALICE, RETURN, None)
    again = await gw.oauth_start(_integration(oauth_platform), USER, ALICE, RETURN, first["client"])
    assert again["client"] is None
    assert _query(again["authorize_url"])["client_id"] == first["client"]["client_id"]
    other = RETURN.replace("192.168.0.108", "10.13.13.1")
    moved = await gw.oauth_start(_integration(oauth_platform), USER, ALICE, other, first["client"])
    assert moved["client"]["redirect_uri"] == other


async def test_an_expiring_token_is_refreshed_and_written_back(gw, oauth_platform, provider):
    provider.access_ttl = 0
    done = await _sign_in(gw, oauth_platform, provider)
    oauth_platform.ciphertext = done["ciphertext"]
    oauth_platform.stored.clear()

    opened = await gw.open(DELEGATION, "home", "chat")
    assert opened["ok"], opened
    assert provider.token_requests[-1]["grant_type"] == "refresh_token"
    assert len(oauth_platform.stored) == 1
    assert oauth_platform.stored[0][0] == USER


async def test_a_refused_token_is_renewed_once_and_retried(gw, oauth_platform, provider):
    done = await _sign_in(gw, oauth_platform, provider)
    oauth_platform.ciphertext = done["ciphertext"]
    provider.expire_access()

    opened = await gw.open(DELEGATION, "home", "chat")
    assert opened["ok"], opened
    refreshes = [r for r in provider.token_requests if r["grant_type"] == "refresh_token"]
    assert len(refreshes) == 1


async def test_a_refused_refresh_needs_reconnecting(gw, oauth_platform, provider):
    done = await _sign_in(gw, oauth_platform, provider)
    oauth_platform.ciphertext = done["ciphertext"]
    provider.expire_access()
    provider.refuse_refresh = True

    opened = await gw.open(DELEGATION, "home", "chat")
    assert opened["error"]["code"] == "auth_expired"
    assert oauth_platform.reconnect == [(INTEGRATION, USER)]


async def test_concurrent_calls_refresh_once(gw, oauth_platform, provider):
    provider.access_ttl = 0
    done = await _sign_in(gw, oauth_platform, provider)
    oauth_platform.ciphertext = done["ciphertext"]
    provider.access_ttl = 1800

    results = await asyncio.gather(*(gw.open(DELEGATION, "home", "chat") for _ in range(4)))
    assert all(r["ok"] for r in results), results
    refreshes = [r for r in provider.token_requests if r["grant_type"] == "refresh_token"]
    assert len(refreshes) == 1


async def test_revoking(gw, oauth_platform, provider):
    done = await _sign_in(gw, oauth_platform, provider)
    credential = {"user_id": str(USER), "ciphertext": done["ciphertext"]}
    revoked = await gw.oauth_revoke(_integration(oauth_platform), credential)
    assert revoked == {"revoked": provider.mode == "ha"}
    if provider.mode == "ha":
        assert provider.revoked and provider.revoked[0].startswith("rt-")


async def test_metadata_without_pkce_is_refused(gw, oauth_platform, provider, monkeypatch):
    plain = provider.metadata

    def no_pkce():
        return {**plain(), "code_challenge_methods_supported": ["plain"]}

    monkeypatch.setattr(provider, "metadata", no_pkce)
    with pytest.raises(OAuthError) as refused:
        await gw.oauth_start(_integration(oauth_platform), USER, ALICE, RETURN, None)
    assert refused.value.code == "oauth_unsupported"


def test_endpoints_must_be_https_unless_on_the_integrations_own_host():
    integration = {"url": "http://192.168.0.108:8123/api/mcp"}
    assert endpoint_allowed("http://192.168.0.108:8123/auth/token", integration)
    assert endpoint_allowed("https://api.example/token", integration)
    assert not endpoint_allowed("http://api.example/token", integration)
    assert not endpoint_allowed("http://192.168.0.108:80/token", integration)


def test_return_page_shape():
    parts = urlsplit(RETURN)
    assert urlunsplit((parts.scheme, parts.netloc, CLIENT_ID_PATH, "", "")).startswith("http://")

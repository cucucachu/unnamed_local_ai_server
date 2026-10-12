"""Over real HTTP: the fake server behind uvicorn, the real connector, and the gateway's own API."""

from __future__ import annotations

import base64
import socket
import threading
import time
from collections.abc import Iterator

import httpx2
import pytest
import uvicorn
from starlette.responses import JSONResponse

from app.config import Settings
from app.connector import HttpConnector, proxy_url
from app.handles import InputHandles
from app.main import build
from app.service import Gateway
from tests import fake_server
from tests.conftest import DELEGATION, INTEGRATION, TOKEN, USER, FakePlatform

PLATFORM_TOKEN = "platform-to-gateway"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def server_url() -> Iterator[str]:
    inner = fake_server.build().streamable_http_app(streamable_http_path="/mcp")

    async def app(scope, receive, send):
        if scope["type"] == "http":
            auth = dict(scope["headers"]).get(b"authorization")
            if auth != f"Bearer {TOKEN}".encode():
                await JSONResponse({"error": "unauthorized"}, status_code=401)(scope, receive, send)
                return
        await inner(scope, receive, send)

    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(app, port=port, log_level="error", lifespan="on"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started:
        assert time.monotonic() < deadline, "fake server didn't start"
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}/mcp"
    server.should_exit = True
    thread.join(5)


@pytest.fixture
def http_platform(key, server_url) -> FakePlatform:
    platform = FakePlatform(key, "2026-07-28")
    original = platform.grant

    async def grant(delegation: str, integration: str):
        g = await original(delegation, integration)
        g["integration"]["url"] = platform.url
        return g

    platform.url = server_url
    platform.grant = grant
    return platform


@pytest.fixture
def api(key, http_platform) -> httpx2.AsyncClient:
    settings = Settings(
        integrations_gateway_token=PLATFORM_TOKEN, call_timeout_s=5.0, result_max_chars=3000
    )
    gateway = Gateway(
        settings,
        key,
        http_platform,
        HttpConnector("", "", connect_timeout_s=2.0, read_timeout_s=5.0),
        InputHandles(60),
    )
    app = build(settings, gateway)
    return httpx2.AsyncClient(transport=httpx2.ASGITransport(app=app), base_url="http://gw")


def agent(delegation: str = DELEGATION) -> dict[str, str]:
    return {"Authorization": f"Bearer {delegation}"}


def platform_auth() -> dict[str, str]:
    return {"Authorization": f"Bearer {PLATFORM_TOKEN}"}


async def test_open_and_call_over_http(api, http_platform):
    async with api:
        opened = await api.post("/v1/open", json={"integration": "home"}, headers=agent())
        assert opened.status_code == 200
        assert "turn_on" in {t["name"] for t in opened.json()["tools"]}

        args = {"room": "office"}
        refused = await api.post(
            "/v1/call",
            json={"integration": "home", "tool": "turn_on", "arguments": args},
            headers=agent(),
        )
        assert refused.json()["error"]["code"] == "approval_required"

        marker = http_platform.mint("turn_on", args)
        ran = await api.post(
            "/v1/call",
            json={"integration": "home", "tool": "turn_on", "arguments": args, "approval": marker},
            headers=agent(),
        )
        assert ran.json() == {
            "ok": True,
            "is_error": False,
            "text": "office on at 100%",
            "trimmed": False,
        }


async def test_rejected_credential_marks_needs_reconnecting(api, http_platform, key):
    http_platform.ciphertext = base64.b64encode(
        key.seal(INTEGRATION, USER, {"kind": "token", "value": "wrong"})
    ).decode()
    async with api:
        r = await api.post("/v1/open", json={"integration": "home"}, headers=agent())
    assert r.json()["error"]["code"] == "auth_expired"
    assert http_platform.reconnect == [(INTEGRATION, USER)]


async def test_unreachable_server(api, http_platform):
    http_platform.url = f"http://127.0.0.1:{_free_port()}/mcp"
    async with api:
        r = await api.post("/v1/open", json={"integration": "home"}, headers=agent())
    assert r.json()["error"]["code"] == "unreachable"


async def test_bad_delegation_is_401(api):
    async with api:
        r = await api.post("/v1/open", json={"integration": "home"}, headers=agent("forged"))
        assert r.status_code == 401
        r = await api.post("/v1/open", json={"integration": "home"})
        assert r.status_code == 401


async def test_encrypt_and_discover_for_the_platform(api, key, server_url):
    async with api:
        denied = await api.post(
            "/encrypt",
            json={"integration_id": str(INTEGRATION), "user_id": str(USER), "secret": TOKEN},
            headers=agent(),
        )
        assert denied.status_code == 401

        sealed = await api.post(
            "/encrypt",
            json={"integration_id": str(INTEGRATION), "user_id": str(USER), "secret": TOKEN},
            headers=platform_auth(),
        )
        assert sealed.status_code == 200
        ciphertext = sealed.json()["ciphertext"]
        assert key.open(base64.b64decode(ciphertext), INTEGRATION, USER)["value"] == TOKEN

        integration = {
            "id": str(INTEGRATION),
            "slug": "home",
            "url": server_url,
            "protocol_version": None,
            "credential_model": "per_user",
        }
        found = await api.post(
            "/discover",
            json={
                "integration": integration,
                "credential": {"user_id": str(USER), "ciphertext": ciphertext},
            },
            headers=platform_auth(),
        )
        assert found.status_code == 200
        assert found.json()["protocol_version"] == "2026-07-28"
        assert "turn_on" in {t["name"] for t in found.json()["tools"]}

        anonymous = await api.post(
            "/discover",
            json={"integration": integration, "credential": None},
            headers=platform_auth(),
        )
        assert anonymous.status_code == 422
        assert anonymous.json() == {"detail": "auth_expired"}


async def test_encrypt_refused_when_token_unset(key):
    app = build(Settings(integrations_gateway_token=""), gateway=object())
    async with httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=app), base_url="http://gw"
    ) as c:
        r = await c.post(
            "/encrypt",
            json={"integration_id": str(INTEGRATION), "user_id": None, "secret": "x"},
            headers={"Authorization": "Bearer "},
        )
    assert r.status_code == 401


@pytest.mark.parametrize(
    "headers,code",
    [
        ({"X-Egress-Denied": "certificate_changed"}, "certificate_changed"),
        ({"X-Egress-Denied": "not_registered"}, "unreachable"),
        ({"X-Egress-Denied": "certificate_untrusted"}, "unreachable"),
        ({}, "auth_expired"),
    ],
)
async def test_proxy_refusals_are_not_mistaken_for_the_server(key, http_platform, headers, code):
    def refuse(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(403, json={"error": "no"}, headers=headers)

    connector = HttpConnector(
        None, "", connect_timeout_s=2.0, read_timeout_s=5.0, transport=httpx2.MockTransport(refuse)
    )
    gateway = Gateway(Settings(call_timeout_s=5.0), key, http_platform, connector, InputHandles(60))
    body = await gateway.open(DELEGATION, "home", "chat")
    assert body["error"]["code"] == code
    assert http_platform.reconnect == ([(INTEGRATION, USER)] if code == "auth_expired" else [])


async def test_proxy_error_page_is_unreachable(key, http_platform):
    def proxy_502(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(502, text="Cannot connect", headers={"Server": "mitmproxy 12.2.3"})

    connector = HttpConnector(
        None,
        "",
        connect_timeout_s=2.0,
        read_timeout_s=5.0,
        transport=httpx2.MockTransport(proxy_502),
    )
    gateway = Gateway(Settings(call_timeout_s=5.0), key, http_platform, connector, InputHandles(60))
    body = await gateway.open(DELEGATION, "home", "chat")
    assert body["error"]["code"] == "unreachable"


def test_proxy_url_carries_the_gateway_credential():
    assert (
        proxy_url("http://egress-proxy:8080", "a/b+c")
        == "http://gateway:a%2Fb%2Bc@egress-proxy:8080"
    )
    assert proxy_url("http://egress-proxy:8080", "") == "http://egress-proxy:8080"
    assert proxy_url("", "t") is None

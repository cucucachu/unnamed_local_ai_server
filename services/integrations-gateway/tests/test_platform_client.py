from __future__ import annotations

from uuid import uuid4

import httpx2
import pytest

from app.errors import GatewayError, PlatformUnavailable, Unauthorized
from app.platform import HttpPlatform


def platform(handler) -> HttpPlatform:
    return HttpPlatform("http://platform:8100", "gw-token", transport=httpx2.MockTransport(handler))


async def test_grant_sends_the_gateway_token_and_maps_errors():
    seen = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        status = {"forged": 401, "missing": 404, "boom": 500}.get(
            request.read().decode().split('"')[3], 200
        )
        return httpx2.Response(status, json={"status": "connected"})

    p = platform(handler)
    assert (await p.grant("good", "home"))["status"] == "connected"
    assert seen[0].headers["authorization"] == "Bearer gw-token"
    assert seen[0].url.path == "/internal/integration-grants"
    with pytest.raises(Unauthorized):
        await p.grant("forged", "home")
    with pytest.raises(GatewayError) as err:
        await p.grant("missing", "home")
    assert err.value.code == "not_found"
    with pytest.raises(PlatformUnavailable):
        await p.grant("boom", "home")


@pytest.mark.parametrize("status,expected", [(204, True), (403, False), (404, False)])
async def test_consume(status, expected):
    p = platform(lambda r: httpx2.Response(status))
    assert await p.consume_approval("d", "t", "home", "turn_on", {}) is expected


async def test_best_effort_calls_survive_an_unreachable_platform():
    def handler(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError("down")

    p = platform(handler)
    await p.audit("d", {"action": "call"})
    await p.mark_needs_reconnecting(uuid4(), None)
    await p.store_discovery(uuid4(), {})
    with pytest.raises(PlatformUnavailable):
        await p.grant("d", "home")

"""Where a turn's tool policy comes from (#342): the platform, read with the turn's delegation."""

from __future__ import annotations

import asyncio
import re
from types import SimpleNamespace

import httpx
import pytest
import respx

from app.agent import tool_catalog
from app.agent.approvals import (
    PlatformToolPolicies,
    PolicyUnavailable,
    StaticToolPolicies,
    ToolPolicy,
)
from tests.fake_model.scripting import FakeModel, TextTurn
from tests.fake_platform.scripting import FakePlatform
from tests.test_chat_ws import _drain_turn, _make_client

PLATFORM = "http://platform:8100"
POLICY_URL = f"{PLATFORM}/api/platform/tools/policy"


def _entry(available: bool, permission: str) -> dict:
    return {"available": available, "permission": permission, "can_always_allow": True}


@respx.mock
async def test_the_policy_is_read_with_the_turns_delegation() -> None:
    route = respx.get(POLICY_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "tools": {
                    "ls": _entry(True, "allow"),
                    "write_file": _entry(True, "ask"),
                    "web_fetch": _entry(False, "allow"),
                    "not_ours": _entry(True, "allow"),
                }
            },
        )
    )
    policy = await PlatformToolPolicies(PLATFORM).for_turn(SimpleNamespace(token="deleg-1"))
    assert policy == ToolPolicy(frozenset({"ls", "write_file"}), frozenset({"ls"}))
    assert route.calls[0].request.headers["Authorization"] == "Bearer deleg-1"


async def test_without_a_delegation_there_is_no_policy() -> None:
    assert await PlatformToolPolicies(PLATFORM).for_turn(None) is None


@pytest.mark.parametrize(
    "response",
    [httpx.Response(503), httpx.Response(200, json={"tools": []}), httpx.ConnectError("down")],
)
@respx.mock
async def test_an_unreadable_policy_is_an_error(response) -> None:
    route = respx.get(POLICY_URL)
    if isinstance(response, Exception):
        route.mock(side_effect=response)
    else:
        route.mock(return_value=response)
    with pytest.raises(PolicyUnavailable):
        await PlatformToolPolicies(PLATFORM).for_turn(SimpleNamespace(token="deleg-1"))


class _Unavailable:
    async def for_turn(self, delegation):
        raise PolicyUnavailable("HTTP 503")


async def test_a_turn_without_its_policy_fails_instead_of_guessing(
    fake_model: FakeModel, fake_platform: FakePlatform
) -> None:
    fake_model.queue(TextTurn("hi"))
    with (
        _make_client(fake_model, fake_platform, tool_policies=_Unavailable()) as client,
        client.websocket_connect("/ws/chat/no-policy") as ws,
    ):
        ws.send_json({"type": "user_message", "content": "hi"})
        frames = _drain_turn(ws)
    assert frames[-1]["type"] in ("error", "turn_end")
    assert any("tool settings" in (f.get("message") or "") for f in frames)
    assert fake_model.requests == []


def test_static_policies_default_to_the_catalogs_defaults() -> None:
    policy = asyncio.run(StaticToolPolicies().for_turn(None))
    read_only = {t.name for t in tool_catalog.CATALOG if t.read_only}
    assert policy == ToolPolicy(tool_catalog.NAMES, frozenset(read_only))


@respx.mock
async def test_the_catalog_is_registered_retrying_until_the_platform_answers(monkeypatch) -> None:
    monkeypatch.setattr(tool_catalog, "REGISTER_RETRY_S", 0)
    route = respx.put(f"{PLATFORM}/internal/tool-catalog").mock(
        side_effect=[httpx.ConnectError("starting"), httpx.Response(503), httpx.Response(204)]
    )
    await asyncio.wait_for(tool_catalog.register(PLATFORM, "agent-token"), 5)
    assert route.call_count == 3
    sent = route.calls[-1].request
    assert sent.headers["Authorization"] == "Bearer agent-token"
    tools = httpx.Response(200, content=sent.content).json()["tools"]
    assert [t["name"] for t in tools] == [t.name for t in tool_catalog.CATALOG]
    assert set(tools[0]) == {"name", "label", "group", "read_only", "description"}


def test_the_catalog_fits_the_platforms_limits() -> None:
    assert all(re.fullmatch(r"[a-z][a-z0-9_]{0,63}", n) for n in tool_catalog.NAMES)
    assert len(tool_catalog.NAMES) == len(tool_catalog.CATALOG)
    for t in tool_catalog.CATALOG:
        assert 0 < len(t.label) <= 80 and len(t.description) <= 500 and 0 < len(t.group) <= 40

"""Integration calls in routine runs (M18-06): the same rules as chats; a tool that asks waits."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from langgraph.checkpoint.memory import MemorySaver

from app.db.settings import InMemorySettingsStore
from app.db.threads import InMemoryThreadStore
from app.main import create_app
from tests import test_routine_scheduler as scheduler_tests
from tests.fake_gateway import FakeGateway
from tests.fake_model.scripting import FakeModel, TextTurn, ToolCallTurn
from tests.fake_platform.scripting import AGENT_TOKEN, FakePlatform
from tests.fake_tool_policies import FakeToolPolicies
from tests.test_chat_ws import _drain_turn
from tests.test_routine_approvals import _approve_all, _pending, _run_once, _wait_for_status
from tests.test_routine_scheduler import ALICE, HEADERS

harness = scheduler_tests.harness
_fixed_now = scheduler_tests._fixed_now
tool_policies = scheduler_tests.tool_policies

TURN_ON = {"integration": "home", "tool": "turn_on", "arguments": {"entity": "light.porch"}}


@pytest.fixture
def client(
    fake_model: FakeModel,
    fake_platform: FakePlatform,
    fake_gateway: FakeGateway,
    tool_policies: FakeToolPolicies,
):
    fake_platform.identities["alice-token"] = (ALICE, "s-alice")
    app = create_app(
        fake_model.settings(
            platform_url=fake_platform.base_url,
            routines_scheduler_enabled=False,
            integrations_gateway_url=fake_gateway.base_url,
            platform_agent_token=AGENT_TOKEN,
        ),
        checkpointer_override=MemorySaver(),
        thread_store_override=InMemoryThreadStore(),
        settings_store_override=InMemorySettingsStore(),
        identity_verifier_override=scheduler_tests.Verifier(),
        delegation_client_override=fake_platform.client(),
        tool_policies_override=tool_policies,
    )
    with TestClient(app) as test_client:
        yield test_client


def test_a_run_stops_at_an_integration_tool_that_asks(
    client, harness, fake_model, fake_gateway: FakeGateway
) -> None:
    routine, run = _run_once(client, harness, fake_model, ToolCallTurn("use_integration", TURN_ON))
    assert run["status"] == "waiting_approval"
    assert fake_gateway.executed == []
    pending = _pending(client, run["thread_id"])
    assert [a["name"] for a in pending["actions"]] == ["use_integration"]
    assert {body["context"] for _, body in fake_gateway.requests} == {"routine"}

    fake_model.queue(TextTurn("porch light on"))
    with client.websocket_connect(f"/ws/chat/{run['thread_id']}", headers=HEADERS) as ws:
        ws.send_json(_approve_all(pending))
        frames = _drain_turn(ws)
    assert frames[-1]["status"] == "completed"
    assert fake_gateway.executed == [("turn_on", {"entity": "light.porch"}, "approved", None)]
    assert _wait_for_status(client, routine["id"], "succeeded")["status"] == "succeeded"


def test_an_allowed_integration_tool_runs_in_a_run(
    client, harness, fake_model, fake_gateway: FakeGateway
) -> None:
    _, run = _run_once(
        client,
        harness,
        fake_model,
        ToolCallTurn("use_integration", {"integration": "home", "tool": "get_state"}),
        TextTurn("all off"),
    )
    assert run["status"] == "succeeded"
    assert fake_gateway.executed == [("get_state", {}, "allowed", None)]

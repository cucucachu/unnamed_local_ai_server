from __future__ import annotations

import asyncio
import base64
import logging
import os
from uuid import uuid4

import pytest

from app.connector import Session
from app.crypto import CredentialError, CredentialKey
from app.errors import MESSAGES, Unauthorized
from app.handles import InputHandles
from app.service import redact
from tests import fake_server
from tests.conftest import DELEGATION, INTEGRATION, TOKEN, USER, FakePlatform


def code(body: dict) -> str | None:
    return None if body["ok"] else body["error"]["code"]


# --- open, call, read, prompt ---------------------------------------------------------------


async def test_open_lists_only_available_tools(gateway, platform: FakePlatform):
    body = await gateway.open(DELEGATION, "home", "chat")
    assert body["ok"], body
    names = {t["name"] for t in body["tools"]}
    assert "hidden_tool" not in names
    assert {"list_lights", "turn_on", "set_scene"} <= names
    turn_on = next(t for t in body["tools"] if t["name"] == "turn_on")
    assert turn_on["permission"] == "ask"
    assert turn_on["input_schema"]["properties"]["room"]["type"] == "string"
    assert body["instructions"] == "Lights are named by room."
    assert body["resources"] == [
        {"uri": "home://rooms", "name": "rooms", "description": "The rooms in the house"}
    ]
    assert body["prompts"][0]["name"] == "evening"
    assert platform.audits[-1] == {
        "integration": "home",
        "action": "open",
        "target": "",
        "arguments": {},
        "context": "chat",
        "approval": None,
        "status": "ok",
        "error_code": None,
    }


async def test_tools_the_registry_doesnt_know_stay_hidden(gateway, platform: FakePlatform):
    del platform.tools["big"]
    body = await gateway.open(DELEGATION, "home", "chat")
    assert "big" not in {t["name"] for t in body["tools"]}
    assert code(await gateway.call(DELEGATION, "home", "big", {}, "chat")) == "not_found"


async def test_speaks_the_recorded_version_and_declares_elicitation_only_to_modern(
    gateway, connector, protocol_version
):
    await gateway.call(DELEGATION, "home", "list_lights", {}, "chat")
    await gateway.read(DELEGATION, "home", "home://rooms", "chat")
    assert connector.versions == [protocol_version, protocol_version]
    call_caps, read_caps = connector.capabilities
    assert (call_caps.elicitation is not None) == (protocol_version == "2026-07-28")
    assert read_caps.elicitation is None


async def test_allowed_tool_runs_without_a_marker(gateway, platform: FakePlatform):
    body = await gateway.call(DELEGATION, "home", "list_lights", {}, "chat")
    assert body == {"ok": True, "is_error": False, "text": "none", "trimmed": False}
    assert platform.audits[-1]["approval"] == "allowed"
    assert platform.audits[-1]["target"] == "list_lights"


async def test_ask_tool_refused_without_a_marker(gateway, platform: FakePlatform):
    body = await gateway.call(DELEGATION, "home", "turn_on", {"room": "kitchen"}, "routine")
    assert code(body) == "approval_required"
    assert body["error"]["message"].startswith(MESSAGES["approval_required"])
    assert fake_server.LIGHTS == {}
    assert platform.audits[-1]["status"] == "error"
    assert platform.audits[-1]["error_code"] == "approval_required"
    assert platform.audits[-1]["context"] == "routine"


async def test_ask_tool_runs_once_with_its_marker(gateway, platform: FakePlatform):
    args = {"room": "kitchen", "brightness": 40}
    marker = platform.mint("turn_on", args)
    body = await gateway.call(DELEGATION, "home", "turn_on", args, "chat", approval=marker)
    assert body["ok"] and body["text"] == "kitchen on at 40%"
    assert fake_server.LIGHTS == {"kitchen": True}
    assert platform.audits[-1]["approval"] == "approved"

    again = await gateway.call(DELEGATION, "home", "turn_on", args, "chat", approval=marker)
    assert code(again) == "approval_required"


async def test_marker_is_bound_to_the_exact_arguments(gateway, platform: FakePlatform):
    marker = platform.mint("turn_on", {"room": "kitchen"})
    body = await gateway.call(
        DELEGATION, "home", "turn_on", {"room": "office"}, "chat", approval=marker
    )
    assert code(body) == "approval_required"
    assert fake_server.LIGHTS == {}


async def test_invalid_arguments_dont_spend_the_marker(gateway, platform: FakePlatform):
    marker = platform.mint("turn_on", {"room": 5})
    body = await gateway.call(DELEGATION, "home", "turn_on", {"room": 5}, "chat", approval=marker)
    assert code(body) == "invalid_arguments"
    assert body["error"]["errors"] == [{"path": "/room", "message": "5 is not of type 'string'"}]
    assert marker in platform.markers

    missing = await gateway.call(DELEGATION, "home", "turn_on", {}, "chat")
    assert code(missing) == "invalid_arguments"
    assert "'room' is a required property" in missing["error"]["errors"][0]["message"]


async def test_tool_turned_off_or_unknown(gateway, platform: FakePlatform):
    off = await gateway.call(DELEGATION, "home", "hidden_tool", {}, "chat")
    assert code(off) == "refused_by_policy"
    unknown = await gateway.call(DELEGATION, "home", "nope", {}, "chat")
    assert code(unknown) == "not_found"
    platform.tools["gone"] = {"available": True, "permission": "allow", "can_always_allow": True}
    gone = await gateway.call(DELEGATION, "home", "gone", {}, "chat")
    assert code(gone) == "not_found"


async def test_server_tool_error_passes_through(gateway):
    body = await gateway.call(DELEGATION, "home", "broken", {}, "chat")
    assert body["ok"] and body["is_error"]
    assert "broken" in body["text"]


async def test_slow_call_times_out(gateway, platform: FakePlatform):
    body = await gateway.call(DELEGATION, "home", "slow", {}, "chat")
    assert code(body) == "timeout"
    assert platform.audits[-1]["error_code"] == "timeout"


async def test_large_result_is_trimmed_head_and_tail(gateway, settings):
    body = await gateway.call(DELEGATION, "home", "big", {}, "chat")
    assert body["ok"] and body["trimmed"]
    text = body["text"]
    assert text.startswith("H" * 1000) and text.endswith("T" * 1000)
    assert "characters trimmed" in text
    assert len(text) < settings.result_max_chars + 200


async def test_read_resource(gateway, platform: FakePlatform):
    body = await gateway.read(DELEGATION, "home", "home://rooms", "chat")
    assert body == {"ok": True, "text": "kitchen, office", "trimmed": False}
    assert platform.audits[-1]["action"] == "read"
    assert platform.audits[-1]["target"] == "home://rooms"
    missing = await gateway.read(DELEGATION, "home", "home://garage", "chat")
    assert code(missing) == "server_error"


async def test_get_prompt(gateway, platform: FakePlatform):
    body = await gateway.prompt(DELEGATION, "home", "evening", {"room": "office"}, "chat")
    assert body["ok"]
    assert body["text"] == "user: Dim the office lights to 30%."
    assert platform.audits[-1]["action"] == "prompt"
    bad = await gateway.prompt(DELEGATION, "home", "evening", {"room": 3}, "chat")
    assert code(bad) == "invalid_arguments"


# --- connection and credentials --------------------------------------------------------------


async def test_credential_header_reaches_the_server(gateway, connector):
    await gateway.call(DELEGATION, "home", "list_lights", {}, "chat")
    assert connector.headers[-1] == {"Authorization": f"Bearer {TOKEN}"}


async def test_not_connected(gateway, platform: FakePlatform, connector):
    platform.status = "not_connected"
    body = await gateway.call(DELEGATION, "home", "list_lights", {}, "chat")
    assert code(body) == "not_connected"
    assert connector.headers == []
    assert platform.audits[-1]["error_code"] == "not_connected"


async def test_needs_reconnecting(gateway, platform: FakePlatform):
    platform.status = "needs_reconnecting"
    assert code(await gateway.open(DELEGATION, "home", "chat")) == "auth_expired"


async def test_credential_from_another_user_doesnt_open(gateway, platform: FakePlatform, key):
    platform.ciphertext = base64.b64encode(
        key.seal(INTEGRATION, uuid4(), {"kind": "token", "value": TOKEN})
    ).decode()
    body = await gateway.call(DELEGATION, "home", "list_lights", {}, "chat")
    assert code(body) == "auth_expired"
    assert platform.reconnect == [(INTEGRATION, USER)]


async def test_shared_credential_used_for_every_user(gateway, platform: FakePlatform, key):
    platform.credential_model = "shared"
    platform.ciphertext = base64.b64encode(
        key.seal(INTEGRATION, None, {"kind": "token", "value": "shared-token"})
    ).decode()
    body = await gateway.call(DELEGATION, "home", "list_lights", {}, "chat")
    assert body["ok"], body


async def test_bad_delegation(gateway):
    with pytest.raises(Unauthorized):
        await gateway.open("forged", "home", "chat")


async def test_unknown_integration(gateway):
    assert code(await gateway.open(DELEGATION, "garage", "chat")) == "not_found"


async def test_discover(gateway, platform: FakePlatform):
    found = await gateway.discover(
        (await platform.grant(DELEGATION, "home"))["integration"],
        {"user_id": str(USER), "ciphertext": platform.ciphertext},
    )
    assert found["protocol_version"] in ("2026-07-28", "2025-11-25")
    assert found["server"]["name"] == "fake-home"
    assert found["instructions"] == "Lights are named by room."
    tools = {t["name"]: t for t in found["tools"]}
    assert tools["list_lights"]["annotations"] == {"readOnlyHint": True}
    assert tools["turn_on"]["input_schema"]["required"] == ["room"]
    assert found["resources"][0]["uri"] == "home://rooms"
    assert found["prompts"][0]["arguments"][0]["name"] == "room"


@pytest.mark.parametrize("model", ["shared", "per_user"])
async def test_list_changed_rediscovers_when_one_credential_sees_everything(
    gateway, platform: FakePlatform, model
):
    platform.credential_model = model
    if model == "shared":
        platform.ciphertext = base64.b64encode(
            platform.key.seal(INTEGRATION, None, {"kind": "token", "value": TOKEN})
        ).decode()
    grant = await platform.grant(DELEGATION, "home")
    gateway._after(grant, {}, Session(client=None, list_changed={"tools"}))
    await asyncio.gather(*gateway._background)
    if model == "per_user":
        assert platform.discoveries == []
    else:
        assert [t["name"] for t in platform.discoveries[0]["tools"]][:2] == [
            "list_lights",
            "turn_on",
        ]


async def test_cancelled_call_is_audited(gateway, platform: FakePlatform):
    task = asyncio.create_task(gateway.call(DELEGATION, "home", "slow", {}, "chat"))
    await asyncio.sleep(0.5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert platform.audits[-1]["error_code"] == "cancelled"


# --- input from the user (elicitation) -------------------------------------------------------


@pytest.mark.parametrize("protocol_version", ["2026-07-28"])
async def test_needs_input_answered_after_a_new_user_message(gateway, platform: FakePlatform):
    first = await gateway.call(
        DELEGATION, "home", "set_scene", {"name": "movie"}, "chat", latest_user_message="m1"
    )
    assert code(first) == "needs_input"
    handle = first["error"]["handle"]
    assert first["error"]["requests"]["room"]["mode"] == "form"
    assert first["error"]["requests"]["room"]["message"] == "Which room?"

    answer = {"room": {"action": "accept", "content": {"room": "office"}}}
    same_turn = await gateway.call(
        DELEGATION,
        "home",
        "set_scene",
        {},
        "chat",
        input_handle=handle,
        input_responses=answer,
        latest_user_message="m1",
    )
    assert code(same_turn) == "refused_by_policy"

    later = await gateway.call(
        DELEGATION,
        "home",
        "set_scene",
        {},
        "chat",
        input_handle=handle,
        input_responses=answer,
        latest_user_message="m2",
    )
    assert later["ok"], later
    assert later["text"] == "scene movie in office (state scene:movie)"
    assert platform.audits[-1]["arguments"] == {"name": "movie"}

    reused = await gateway.call(
        DELEGATION,
        "home",
        "set_scene",
        {},
        "chat",
        input_handle=handle,
        input_responses=answer,
        latest_user_message="m3",
    )
    assert code(reused) == "not_found"


@pytest.mark.parametrize("protocol_version", ["2026-07-28"])
async def test_needs_input_declined(gateway):
    first = await gateway.call(
        DELEGATION, "home", "set_scene", {"name": "movie"}, "chat", latest_user_message="m1"
    )
    body = await gateway.call(
        DELEGATION,
        "home",
        "set_scene",
        {},
        "chat",
        input_handle=first["error"]["handle"],
        input_responses={"room": {"action": "decline"}},
        latest_user_message="m2",
    )
    assert body["ok"] and body["text"] == "scene movie declined"


@pytest.mark.parametrize("protocol_version", ["2026-07-28"])
async def test_needs_input_handle_bound_to_thread(gateway, platform: FakePlatform):
    first = await gateway.call(
        DELEGATION, "home", "set_scene", {"name": "movie"}, "chat", latest_user_message="m1"
    )
    platform.thread_id = "thread-2"
    body = await gateway.call(
        DELEGATION,
        "home",
        "set_scene",
        {},
        "chat",
        input_handle=first["error"]["handle"],
        input_responses={"room": {"action": "decline"}},
        latest_user_message="m2",
    )
    assert code(body) == "not_found"


@pytest.mark.parametrize("protocol_version", ["2025-11-25"])
async def test_older_servers_cant_ask_for_input(gateway, connector):
    body = await gateway.call(
        DELEGATION, "home", "set_scene", {"name": "movie"}, "chat", latest_user_message="m1"
    )
    assert code(body) == "server_error"


def test_handles_expire():
    now = [0.0]
    handles = InputHandles(10, clock=lambda: now[0])
    h = handles.create(
        user_id=USER,
        thread_id="t",
        integration_id=INTEGRATION,
        tool="x",
        arguments={},
        requests={},
        request_state=None,
        asked_after="m1",
        approval="allowed",
    )
    now[0] = 11
    with pytest.raises(Exception) as err:
        handles.take(h, USER, "t", INTEGRATION, "x", "m2")
    assert err.value.code == "not_found"


# --- secrets ---------------------------------------------------------------------------------


async def test_secrets_never_logged_or_audited(gateway, platform: FakePlatform, caplog):
    caplog.set_level(logging.DEBUG)
    marker = platform.mint("turn_on", {"room": "kitchen"})
    await gateway.open(DELEGATION, "home", "chat")
    await gateway.call(DELEGATION, "home", "turn_on", {"room": "kitchen"}, "chat", approval=marker)
    await gateway.call(DELEGATION, "home", "slow", {}, "chat")
    platform.ciphertext = base64.b64encode(os.urandom(64)).decode()
    await gateway.call(DELEGATION, "home", "list_lights", {}, "chat")
    assert TOKEN not in caplog.text
    assert TOKEN not in repr(platform.audits)


def test_redact_hides_secret_looking_arguments():
    assert redact({"room": "kitchen", "api_key": "k", "nested": [{"Password": "p"}]}) == {
        "room": "kitchen",
        "api_key": "[hidden]",
        "nested": [{"Password": "[hidden]"}],
    }


# --- the credential key ----------------------------------------------------------------------


def test_key_file_made_private_and_reused(tmp_path):
    path = tmp_path / "k" / "credentials.key"
    first = CredentialKey.load_or_create(path)
    assert path.stat().st_mode & 0o777 == 0o600
    sealed = first.seal(INTEGRATION, USER, {"kind": "token", "value": "v"})
    assert CredentialKey.load_or_create(path).open(sealed, INTEGRATION, USER)["value"] == "v"


def test_ciphertext_bound_to_integration_and_user(key):
    sealed = key.seal(INTEGRATION, USER, {"kind": "token", "value": "v"})
    assert key.open(sealed, INTEGRATION, USER) == {"kind": "token", "value": "v"}
    for integration, user in ((uuid4(), USER), (INTEGRATION, uuid4()), (INTEGRATION, None)):
        with pytest.raises(CredentialError):
            key.open(sealed, integration, user)
    shared = key.seal(INTEGRATION, None, {"kind": "token", "value": "v"})
    with pytest.raises(CredentialError):
        key.open(shared, INTEGRATION, USER)


def test_other_key_or_tampering_fails(key, tmp_path):
    sealed = key.seal(INTEGRATION, USER, {"kind": "token", "value": "v"})
    other = CredentialKey.load_or_create(tmp_path / "other.key")
    with pytest.raises(CredentialError):
        other.open(sealed, INTEGRATION, USER)
    tampered = sealed[:-1] + bytes([sealed[-1] ^ 1])
    with pytest.raises(CredentialError):
        key.open(tampered, INTEGRATION, USER)
    assert TOKEN.encode() not in key.seal(INTEGRATION, USER, {"kind": "token", "value": TOKEN})

"""M11-02: delegation plumbing on the chat socket (`app/api/chat_ws.py`, `app/core/delegation.py`).

The socket exchanges its identity token at connect, re-mints at every turn
and approval resume, and hands the result to the file tools only through
`configurable["delegation"]`: never the model's context, never the
checkpointer. Runs against the fake platform (`tests/fake_platform/`), over
HTTP for the files API.
"""

from __future__ import annotations

import pickle

import pytest
from langgraph.checkpoint.memory import MemorySaver
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.core.delegation import HttpDelegationClient
from app.db.settings import InMemorySettingsStore
from app.main import create_app
from tests.fake_identity import TEST_USER_ID, AutoCreateThreadStore, FixedIdentityVerifier
from tests.fake_model.scripting import FakeModel, TextTurn, ToolCallTurn
from tests.fake_platform.scripting import AGENT_TOKEN, TEST_SESSION_ID, FakePlatform
from tests.test_chat_ws import _assert_turn_end, _drain_turn

IDENTITY = {"X-HomeAI-Identity": "identity-jwt-alice"}


async def _settings_store(hitl: bool) -> InMemorySettingsStore:
    store = InMemorySettingsStore()
    await store.update_document(TEST_USER_ID, {"hitl_enabled": hitl})
    return store


def _client(
    fake_model: FakeModel,
    fake_platform: FakePlatform,
    settings_store: InMemorySettingsStore,
    *,
    saver: MemorySaver | None = None,
    over_http: bool = False,
) -> TestClient:
    settings = fake_model.settings(
        platform_url=fake_platform.base_url, platform_agent_token=AGENT_TOKEN
    )
    app = create_app(
        settings,
        checkpointer_override=saver or MemorySaver(),
        thread_store_override=AutoCreateThreadStore(),
        settings_store_override=settings_store,
        identity_verifier_override=FixedIdentityVerifier(),
        # `over_http`: the production client against the fake's endpoints.
        delegation_client_override=None if over_http else fake_platform.client(),
    )
    return TestClient(app)


def _write(path: str, content: str = "y") -> ToolCallTurn:
    return ToolCallTurn(name="write_file", args={"file_path": path, "content": content})


def _close_code(ws) -> int:
    with pytest.raises(WebSocketDisconnect) as exc_info:
        ws.receive_json()
    return exc_info.value.code


async def test_exchange_at_connect_refresh_every_turn(
    fake_model: FakeModel, fake_platform: FakePlatform
) -> None:
    fake_model.queue(_write("/personal/a.md"), TextTurn("one"), _write("/personal/b.md"))
    fake_model.queue(TextTurn("two"))

    with (
        _client(fake_model, fake_platform, await _settings_store(False)) as client,
        client.websocket_connect("/ws/chat/dlg-thread", headers=IDENTITY) as ws,
    ):
        assert fake_platform.exchanges == [("identity-jwt-alice", "dlg-thread")]
        assert fake_platform.refreshes == []
        ws.send_json({"type": "user_message", "content": "write a"})
        _assert_turn_end(_drain_turn(ws)[-1], "completed")
        ws.send_json({"type": "user_message", "content": "write b"})
        _assert_turn_end(_drain_turn(ws)[-1], "completed")

    minted = list(fake_platform.grants)
    # connect, then one re-mint per turn; each turn's file call uses that turn's token.
    assert len(minted) == 3
    assert fake_platform.refreshes == minted[:2]
    assert [bearer for _, _, bearer in fake_platform.file_requests] == minted[1:]
    assert all(g.thread_id == "dlg-thread" for g in fake_platform.grants.values())
    assert fake_platform.personal() == {"a.md": b"y", "b.md": b"y"}


async def test_production_client_against_the_platform_endpoints(
    fake_model: FakeModel, fake_platform: FakePlatform
) -> None:
    fake_model.queue(_write("/personal/a.md"), TextTurn("done"))

    with _client(fake_model, fake_platform, await _settings_store(False), over_http=True) as client:
        assert isinstance(client.app.state.delegation_client, HttpDelegationClient)
        with client.websocket_connect("/ws/chat/http-thread", headers=IDENTITY) as ws:
            ws.send_json({"type": "user_message", "content": "write"})
            _assert_turn_end(_drain_turn(ws)[-1], "completed")

    assert fake_platform.exchanges == [("identity-jwt-alice", "http-thread")]
    assert len(fake_platform.refreshes) == 1
    assert fake_platform.personal() == {"a.md": b"y"}


async def test_resume_refreshes_before_running_the_approved_tool(
    fake_model: FakeModel, fake_platform: FakePlatform
) -> None:
    fake_model.queue(_write("/personal/x.txt"))

    with (
        _client(fake_model, fake_platform, await _settings_store(True)) as client,
        client.websocket_connect("/ws/chat/resume-thread", headers=IDENTITY) as ws,
    ):
        ws.send_json({"type": "user_message", "content": "write"})
        frames = _drain_turn(ws)
        approval = next(f for f in frames if f["type"] == "approval_request")
        assert approval["actions"][0]["description"] == "Write file `/personal/x.txt`"
        assert len(fake_platform.refreshes) == 1

        fake_model.queue(TextTurn("done"))
        ws.send_json(
            {
                "type": "approval_response",
                "interrupt_id": approval["interrupt_id"],
                "decisions": [
                    {
                        "tool_call_id": approval["actions"][0]["tool_call_id"],
                        "decision": "approve",
                    }
                ],
            }
        )
        _assert_turn_end(_drain_turn(ws)[-1], "completed")

    assert len(fake_platform.refreshes) == 2
    assert fake_platform.file_requests[-1][2] == list(fake_platform.grants)[-1]
    assert fake_platform.personal() == {"x.txt": b"y"}


async def test_hitl_description_normalizes_a_relative_path(
    fake_model: FakeModel, fake_platform: FakePlatform
) -> None:
    fake_model.queue(_write("personal/notes.md"))

    with (
        _client(fake_model, fake_platform, await _settings_store(True)) as client,
        client.websocket_connect("/ws/chat/rel-thread", headers=IDENTITY) as ws,
    ):
        ws.send_json({"type": "user_message", "content": "write"})
        frames = _drain_turn(ws)

    approval = next(f for f in frames if f["type"] == "approval_request")
    assert approval["actions"][0]["description"] == "Write file `/personal/notes.md`"


async def test_revoked_session_is_refused_at_connect(
    fake_model: FakeModel, fake_platform: FakePlatform
) -> None:
    fake_platform.revoked_sessions.add(TEST_SESSION_ID)

    with (
        _client(fake_model, fake_platform, await _settings_store(False)) as client,
        client.websocket_connect("/ws/chat/revoked-thread", headers=IDENTITY) as ws,
    ):
        assert _close_code(ws) == 4401

    assert fake_platform.grants == {}


async def test_session_revoked_mid_socket_stops_the_next_turn(
    fake_model: FakeModel, fake_platform: FakePlatform
) -> None:
    fake_model.queue(TextTurn("first"))

    with (
        _client(fake_model, fake_platform, await _settings_store(False)) as client,
        client.websocket_connect("/ws/chat/revoke-thread", headers=IDENTITY) as ws,
    ):
        ws.send_json({"type": "user_message", "content": "hi"})
        _assert_turn_end(_drain_turn(ws)[-1], "completed")

        fake_platform.revoked_sessions.add(TEST_SESSION_ID)
        ws.send_json({"type": "user_message", "content": "again"})
        assert _close_code(ws) == 4401

    assert len(fake_model.requests) == 1


async def test_session_revoked_while_awaiting_approval_blocks_the_resume(
    fake_model: FakeModel, fake_platform: FakePlatform
) -> None:
    fake_model.queue(_write("/personal/x.txt"))

    with (
        _client(fake_model, fake_platform, await _settings_store(True)) as client,
        client.websocket_connect("/ws/chat/revoke-resume", headers=IDENTITY) as ws,
    ):
        ws.send_json({"type": "user_message", "content": "write"})
        approval = next(f for f in _drain_turn(ws) if f["type"] == "approval_request")

        fake_platform.revoked_sessions.add(TEST_SESSION_ID)
        ws.send_json(
            {
                "type": "approval_response",
                "interrupt_id": approval["interrupt_id"],
                "decisions": [
                    {
                        "tool_call_id": approval["actions"][0]["tool_call_id"],
                        "decision": "approve",
                    }
                ],
            }
        )
        assert _close_code(ws) == 4401

    assert fake_platform.personal() == {}
    assert fake_platform.file_requests == []


async def test_platform_unavailable_at_connect_closes_1011(
    fake_model: FakeModel, fake_platform: FakePlatform
) -> None:
    fake_platform.unavailable = True

    with (
        _client(fake_model, fake_platform, await _settings_store(False)) as client,
        client.websocket_connect("/ws/chat/down-thread", headers=IDENTITY) as ws,
    ):
        assert _close_code(ws) == 1011


async def test_platform_blip_at_turn_start_uses_the_current_delegation(
    fake_model: FakeModel, fake_platform: FakePlatform
) -> None:
    fake_model.queue(_write("/personal/a.md"), TextTurn("done"))

    with (
        _client(fake_model, fake_platform, await _settings_store(False)) as client,
        client.websocket_connect("/ws/chat/blip-thread", headers=IDENTITY) as ws,
    ):
        fake_platform.unavailable = True
        ws.send_json({"type": "user_message", "content": "write"})
        _assert_turn_end(_drain_turn(ws)[-1], "completed")

    assert [bearer for _, _, bearer in fake_platform.file_requests] == list(fake_platform.grants)
    assert fake_platform.personal() == {"a.md": b"y"}


async def test_delegation_never_reaches_the_model_or_the_checkpointer(
    fake_model: FakeModel, fake_platform: FakePlatform
) -> None:
    fake_model.queue(_write("/personal/a.md"), TextTurn("done"))
    saver = MemorySaver()

    with (
        _client(fake_model, fake_platform, await _settings_store(False), saver=saver) as client,
        client.websocket_connect("/ws/chat/leak-thread", headers=IDENTITY) as ws,
    ):
        ws.send_json({"type": "user_message", "content": "write"})
        frames = _drain_turn(ws)
        _assert_turn_end(frames[-1], "completed")
        history = client.get("/api/threads/leak-thread/messages").json()

    tokens = list(fake_platform.grants)
    assert tokens and fake_platform.file_requests
    stored = pickle.dumps((dict(saver.storage), dict(saver.writes), dict(saver.blobs)))
    checkpoints = list(saver.list({"configurable": {"thread_id": "leak-thread"}}))
    assert checkpoints
    for token in tokens:
        assert token.encode() not in stored
        assert token not in repr(fake_model.requests)
        assert token not in repr(frames)
        assert token not in repr(history)
        for checkpoint in checkpoints:
            assert token not in repr(checkpoint)
    for checkpoint in checkpoints:
        assert "delegation" not in checkpoint.metadata

"""M17-01: a chat turn outlives its socket (`app/agent/turn_runner.py`).

M17-10: how a turn ends (an approval, nobody watching) orders the chats list.

Same synchronous `TestClient` setup as `test_chat_ws.py`; the turns here
stream slowly (`chunk_delay_s`) so a socket can drop and reconnect mid-turn.
"""

import time

from langgraph.checkpoint.memory import MemorySaver
from starlette.testclient import TestClient

from app.db.settings import InMemorySettingsStore
from app.main import create_app
from tests.fake_identity import TEST_USER_ID, AutoCreateThreadStore, FixedIdentityVerifier
from tests.fake_model.scripting import FakeModel, TextTurn, ToolCallTurn
from tests.fake_platform.scripting import FakePlatform

SLOW_REPLY = "one two three four five six seven eight nine ten"


def _make_client(
    fake_model: FakeModel,
    fake_platform: FakePlatform,
    settings_store: InMemorySettingsStore | None = None,
    **settings: object,
) -> TestClient:
    app = create_app(
        fake_model.settings(platform_url=fake_platform.base_url, **settings),
        checkpointer_override=MemorySaver(),
        thread_store_override=AutoCreateThreadStore(),
        settings_store_override=settings_store or InMemorySettingsStore(),
        identity_verifier_override=FixedIdentityVerifier(),
        delegation_client_override=fake_platform.client(),
    )
    return TestClient(app)


def _drain_turn(ws) -> list[dict]:
    frames = []
    while True:
        frame = ws.receive_json()
        frames.append(frame)
        if frame["type"] in ("turn_end", "error"):
            return frames


def _wait_until_idle(client: TestClient, thread_id: str, timeout_s: float = 10) -> None:
    deadline = time.monotonic() + timeout_s
    while client.get(f"/api/threads/{thread_id}/state").json()["running"]:
        assert time.monotonic() < deadline, "turn never finished"
        time.sleep(0.05)


def _tokens(frames: list[dict]) -> str:
    return "".join(f["content"] for f in frames if f["type"] == "token")


def _start_slow_turn(client: TestClient, fake_model: FakeModel, thread_id: str) -> None:
    """Send a message, read the first token, then drop the socket."""
    fake_model.queue(TextTurn(SLOW_REPLY, chunk_size=4, chunk_delay_s=0.15))
    with client.websocket_connect(f"/ws/chat/{thread_id}") as ws:
        ws.send_json({"type": "user_message", "content": "count", "id": "u-1"})
        assert ws.receive_json() == {"type": "turn_start"}
        assert ws.receive_json()["type"] == "token"


async def test_turn_survives_disconnect_and_replays_on_reconnect(
    fake_model: FakeModel, fake_platform: FakePlatform
) -> None:
    thread_id = "detached-replay"
    with _make_client(fake_model, fake_platform) as client:
        _start_slow_turn(client, fake_model, thread_id)
        assert client.get(f"/api/threads/{thread_id}/state").json() == {
            "pending_approval": None,
            "running": True,
        }

        with client.websocket_connect(f"/ws/chat/{thread_id}") as ws:
            frames = _drain_turn(ws)

        assert frames[0] == {
            "type": "turn_start",
            "replay": True,
            "user_message": {"id": "u-1", "content": "count"},
        }
        assert frames[-1]["type"] == "turn_end"
        assert frames[-1]["status"] == "completed"
        # The replay starts from the turn's first frame, so nothing is lost.
        assert _tokens(frames) == SLOW_REPLY

        messages = client.get(f"/api/threads/{thread_id}/messages").json()
        assert [m["role"] for m in messages] == ["user", "assistant"]
        assert messages[-1]["content"] == SLOW_REPLY


async def test_turn_completes_with_no_client(
    fake_model: FakeModel, fake_platform: FakePlatform
) -> None:
    thread_id = "detached-complete"
    with _make_client(fake_model, fake_platform) as client:
        _start_slow_turn(client, fake_model, thread_id)
        _wait_until_idle(client, thread_id)

        messages = client.get(f"/api/threads/{thread_id}/messages").json()
        assert messages[-1]["role"] == "assistant"
        assert messages[-1]["content"] == SLOW_REPLY
        assert messages[-1]["turn"]["status"] == "completed"

        # Idle again: a reconnecting socket gets no replay and can chat as usual.
        fake_model.queue(TextTurn("again"))
        with client.websocket_connect(f"/ws/chat/{thread_id}") as ws:
            ws.send_json({"type": "user_message", "content": "more"})
            frames = _drain_turn(ws)
        assert frames[0] == {"type": "turn_start"}
        assert _tokens(frames) == "again"


async def test_history_mid_turn_is_the_turn_start(
    fake_model: FakeModel, fake_platform: FakePlatform
) -> None:
    """Mid-turn, `/messages` leaves out the running turn (the replay has it)."""
    thread_id = "detached-history"
    with _make_client(fake_model, fake_platform) as client:
        fake_model.queue(TextTurn("first"))
        with client.websocket_connect(f"/ws/chat/{thread_id}") as ws:
            ws.send_json({"type": "user_message", "content": "hello"})
            _drain_turn(ws)

        _start_slow_turn(client, fake_model, thread_id)
        messages = client.get(f"/api/threads/{thread_id}/messages").json()
        assert [m["content"] for m in messages] == ["hello", "first"]
        _wait_until_idle(client, thread_id)


async def test_unwatched_turn_is_cancelled_after_timeout(
    fake_model: FakeModel, fake_platform: FakePlatform
) -> None:
    thread_id = "detached-timeout"
    with _make_client(fake_model, fake_platform, agent_detached_turn_timeout_s=0.3) as client:
        _start_slow_turn(client, fake_model, thread_id)
        _wait_until_idle(client, thread_id, timeout_s=1.2)

        messages = client.get(f"/api/threads/{thread_id}/messages").json()
        assert [m["role"] for m in messages] == ["user"]


async def test_second_socket_follows_the_same_turn(
    fake_model: FakeModel, fake_platform: FakePlatform
) -> None:
    thread_id = "detached-two-sockets"
    fake_model.queue(TextTurn(SLOW_REPLY, chunk_size=4, chunk_delay_s=0.1))
    with _make_client(fake_model, fake_platform) as client, client.websocket_connect(
        f"/ws/chat/{thread_id}"
    ) as first:
        first.send_json({"type": "user_message", "content": "count"})
        assert first.receive_json() == {"type": "turn_start"}
        with client.websocket_connect(f"/ws/chat/{thread_id}") as second:
            second_frames = _drain_turn(second)
        first_frames = _drain_turn(first)

    assert second_frames[0]["replay"] is True
    assert _tokens(second_frames) == SLOW_REPLY
    assert first_frames[-1]["status"] == second_frames[-1]["status"] == "completed"


async def test_cancel_from_a_reattached_socket(
    fake_model: FakeModel, fake_platform: FakePlatform
) -> None:
    thread_id = "detached-cancel"
    with _make_client(fake_model, fake_platform) as client:
        _start_slow_turn(client, fake_model, thread_id)
        with client.websocket_connect(f"/ws/chat/{thread_id}") as ws:
            assert ws.receive_json()["replay"] is True
            ws.send_json({"type": "cancel"})
            frames = _drain_turn(ws)
        assert frames[-1]["type"] == "turn_end"
        assert frames[-1]["status"] == "cancelled"
        assert not client.get(f"/api/threads/{thread_id}/state").json()["running"]


async def test_approval_reached_while_detached_is_restored(
    fake_model: FakeModel, fake_platform: FakePlatform
) -> None:
    thread_id = "detached-approval"
    settings_store = InMemorySettingsStore()
    await settings_store.update_document(TEST_USER_ID, {"hitl_enabled": True})
    fake_model.queue(
        ToolCallTurn(name="write_file", args={"file_path": "/personal/x.txt", "content": "y"}),
        TextTurn("written"),
    )
    with _make_client(fake_model, fake_platform, settings_store) as client:
        with client.websocket_connect(f"/ws/chat/{thread_id}") as ws:
            ws.send_json({"type": "user_message", "content": "write a file"})
            assert ws.receive_json() == {"type": "turn_start"}
        _wait_until_idle(client, thread_id)

        pending = client.get(f"/api/threads/{thread_id}/state").json()["pending_approval"]
        assert pending["actions"][0]["name"] == "write_file"

        with client.websocket_connect(f"/ws/chat/{thread_id}") as ws:
            ws.send_json(
                {
                    "type": "approval_response",
                    "interrupt_id": pending["interrupt_id"],
                    "decisions": [
                        {"tool_call_id": pending["actions"][0]["tool_call_id"], "decision": "approve"}
                    ],
                }
            )
            frames = _drain_turn(ws)

    assert frames[-1]["status"] == "completed"
    assert _tokens(frames) == "written"
    assert fake_platform.personal() == {"x.txt": b"y"}


def _listed(client: TestClient) -> list[tuple[str, bool, bool]]:
    return [
        (t["id"], t["needs_approval"], t["unread"]) for t in client.get("/api/threads").json()
    ]


async def test_the_chats_list_puts_what_needs_you_first(
    fake_model: FakeModel, fake_platform: FakePlatform
) -> None:
    """M17-10: approval first, then unread, then the rest, newest first."""
    settings_store = InMemorySettingsStore()
    await settings_store.update_document(TEST_USER_ID, {"hitl_enabled": True})
    with _make_client(fake_model, fake_platform, settings_store) as client:
        _start_slow_turn(client, fake_model, "away")
        _wait_until_idle(client, "away")

        fake_model.queue(
            ToolCallTurn(name="write_file", args={"file_path": "/personal/x.txt", "content": "y"})
        )
        with client.websocket_connect("/ws/chat/asks") as ws:
            ws.send_json({"type": "user_message", "content": "write a file"})
            _drain_turn(ws)

        fake_model.queue(TextTurn("hi"))
        with client.websocket_connect("/ws/chat/watched") as ws:
            ws.send_json({"type": "user_message", "content": "hello"})
            _drain_turn(ws)

        # Watched to the end: the approval card was seen, so only "away" is unread.
        assert _listed(client) == [
            ("asks", True, False),
            ("away", False, True),
            ("watched", False, False),
        ]

        assert client.post("/api/threads/away/read").status_code == 204
        assert [t[0] for t in _listed(client)] == ["asks", "watched", "away"]

        pending = client.get("/api/threads/asks/state").json()["pending_approval"]
        fake_model.queue(TextTurn("written"))
        with client.websocket_connect("/ws/chat/asks") as ws:
            ws.send_json(
                {
                    "type": "approval_response",
                    "interrupt_id": pending["interrupt_id"],
                    "decisions": [
                        {"tool_call_id": pending["actions"][0]["tool_call_id"], "decision": "approve"}
                    ],
                }
            )
            _drain_turn(ws)
        assert _listed(client)[0] == ("asks", False, False)


async def test_an_approval_reached_while_away_is_unread_and_first(
    fake_model: FakeModel, fake_platform: FakePlatform
) -> None:
    settings_store = InMemorySettingsStore()
    await settings_store.update_document(TEST_USER_ID, {"hitl_enabled": True})
    fake_model.queue(TextTurn("hi"))
    with _make_client(fake_model, fake_platform, settings_store) as client:
        with client.websocket_connect("/ws/chat/other") as ws:
            ws.send_json({"type": "user_message", "content": "hello"})
            _drain_turn(ws)
        fake_model.queue(
            ToolCallTurn(name="write_file", args={"file_path": "/personal/x.txt", "content": "y"})
        )
        with client.websocket_connect("/ws/chat/paused") as ws:
            ws.send_json({"type": "user_message", "content": "write a file"})
            assert ws.receive_json() == {"type": "turn_start"}
        _wait_until_idle(client, "paused")
        assert _listed(client) == [("paused", True, True), ("other", False, False)]

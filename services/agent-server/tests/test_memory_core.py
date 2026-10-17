"""The memory core in every thread (M21-06, docs/MEMORY.md §3), against the fake platform."""

from __future__ import annotations

from app.agent.memory_core import CORE_MAX_CHARS, CUT_NOTE, memory_block
from app.agent.prompts import MEMORY_GUIDE
from tests.fake_identity import TEST_USER_ID
from tests.fake_model.scripting import FakeModel, TextTurn
from tests.fake_platform.scripting import FakePlatform
from tests.test_chat_ws import _make_client
from tests.test_integration_tools import _system, _turn

BOB = "b0b00000-0000-4000-8000-000000000002"
FRAMING = "Your notes about this user, from /personal/Memory/memory.md"


def _note(fake_platform: FakePlatform, text: str, user_id: str = TEST_USER_ID) -> None:
    fake_platform.personal(user_id)["Memory/memory.md"] = text.encode()


async def test_each_user_gets_only_their_own_note(
    fake_model: FakeModel, fake_platform: FakePlatform
) -> None:
    fake_platform.identities["bob-token"] = (BOB, "s-bob")
    _note(fake_platform, "Sister: Ingrid.")
    _note(fake_platform, "Dog: Pepper.", BOB)
    fake_model.queue(TextTurn("a"), TextTurn("b"))
    with _make_client(fake_model, fake_platform) as client:
        with client.websocket_connect("/ws/chat/core-alice") as ws:
            _turn(ws, "hi")
        bob = {"X-HomeAI-Identity": "bob-token"}
        with client.websocket_connect("/ws/chat/core-bob", headers=bob) as ws:
            _turn(ws, "hi")

    alice, bob_system = (_system(r) for r in fake_model.requests)
    assert alice.endswith(memory_block("Sister: Ingrid."))
    assert "Pepper" not in alice
    assert bob_system.endswith(memory_block("Dog: Pepper."))
    assert "Ingrid" not in bob_system


async def test_the_note_is_fixed_for_the_thread(
    fake_model: FakeModel, fake_platform: FakePlatform
) -> None:
    _note(fake_platform, "Lives in Oslo.")
    fake_model.queue(TextTurn("one"), TextTurn("two"), TextTurn("three"))
    with _make_client(fake_model, fake_platform) as client:
        with client.websocket_connect("/ws/chat/core-a") as ws:
            _turn(ws, "hello")
            _note(fake_platform, "Moved to Bergen.")
            _turn(ws, "again")
        with client.websocket_connect("/ws/chat/core-b") as ws:
            _turn(ws, "new chat")

    first, second, other = (_system(r) for r in fake_model.requests)
    assert first == second
    assert first.endswith(memory_block("Lives in Oslo."))
    assert other.endswith(memory_block("Moved to Bergen."))


async def test_a_missing_or_empty_note_shows_no_block(
    fake_model: FakeModel, fake_platform: FakePlatform
) -> None:
    fake_model.queue(TextTurn("one"), TextTurn("two"), TextTurn("three"))
    with _make_client(fake_model, fake_platform) as client:
        with client.websocket_connect("/ws/chat/core-missing") as ws:
            _turn(ws, "hello")
            _turn(ws, "again")
        _note(fake_platform, "  \n")
        with client.websocket_connect("/ws/chat/core-empty") as ws:
            _turn(ws, "hello")

    for request in fake_model.requests:
        system = _system(request)
        assert FRAMING not in system
        assert system.endswith(MEMORY_GUIDE.rstrip("\\\n"))
    # A missing note is an answer: frozen like a real one, not asked for again.
    assert len(fake_platform.memory_core_reads) == 2


def test_an_oversized_note_is_cut_with_a_note() -> None:
    block = memory_block("x" * (CORE_MAX_CHARS + 500))
    assert block.endswith(f"{'x' * 10}\n{CUT_NOTE}")
    assert block.count("x") == CORE_MAX_CHARS
    assert CUT_NOTE not in memory_block("x" * CORE_MAX_CHARS)


async def test_an_oversized_note_on_disk_is_cut(
    fake_model: FakeModel, fake_platform: FakePlatform
) -> None:
    _note(fake_platform, "é" * (CORE_MAX_CHARS * 3))
    fake_model.queue(TextTurn("ok"))
    with (
        _make_client(fake_model, fake_platform) as client,
        client.websocket_connect("/ws/chat/core-big") as ws,
    ):
        _turn(ws, "hi")
    system = _system(fake_model.requests[0])
    assert system.endswith(CUT_NOTE)
    assert system.count("é") == CORE_MAX_CHARS


async def test_a_platform_error_does_not_fail_the_turn_and_is_retried(
    fake_model: FakeModel, fake_platform: FakePlatform
) -> None:
    _note(fake_platform, "Sister: Ingrid.")
    fake_platform.download_error = (503, "unavailable")
    fake_model.queue(TextTurn("one"), TextTurn("two"))
    with (
        _make_client(fake_model, fake_platform) as client,
        client.websocket_connect("/ws/chat/core-retry") as ws,
    ):
        frames = _turn(ws, "hello")
        assert frames[-1]["type"] == "turn_end"
        fake_platform.download_error = None
        _turn(ws, "again")

    first, second = (_system(r) for r in fake_model.requests)
    assert len(fake_platform.memory_core_reads) == 2
    assert FRAMING not in first
    assert second.endswith(memory_block("Sister: Ingrid."))


async def test_the_static_prefix_is_the_same_with_and_without_a_note(
    fake_model: FakeModel, fake_platform: FakePlatform
) -> None:
    fake_model.queue(TextTurn("one"), TextTurn("two"))
    with _make_client(fake_model, fake_platform) as client:
        with client.websocket_connect("/ws/chat/core-without") as ws:
            _turn(ws, "hello")
        _note(fake_platform, "Sister: Ingrid.")
        with client.websocket_connect("/ws/chat/core-with") as ws:
            _turn(ws, "hello")

    without, with_core = fake_model.requests
    assert without["tools"] == with_core["tools"]
    assert _system(with_core) == f"{_system(without)}\n\n{memory_block('Sister: Ingrid.')}"

"""#313: every chat's first model request starts with the same bytes.

Hybrid models (Qwen3.5 / Ornith gated DeltaNet) can only reuse llama.cpp's
prompt cache back to a saved checkpoint, so anything that differs between
chats before the user's message forces a full prefill of the system prompt.
"""

import json

from tests.fake_model.scripting import FakeModel, TextTurn
from tests.fake_platform.scripting import FakePlatform
from tests.test_chat_ws import _drain_turn, _make_client, _no_hitl_settings_store


def _prefix(body: dict) -> str:
    return json.dumps({"tools": body.get("tools"), "system": body["messages"][0]}, sort_keys=False)


async def test_first_request_prefix_is_identical_across_chats(
    fake_model: FakeModel, fake_platform: FakePlatform
) -> None:
    fake_model.queue(TextTurn("one"), TextTurn("two"))
    store = await _no_hitl_settings_store()
    with _make_client(fake_model, fake_platform, settings_store=store) as client:
        for thread, text in (("prefix-a", "make me a grocery app"), ("prefix-b", "what's 2+2?")):
            with client.websocket_connect(f"/ws/chat/{thread}") as ws:
                ws.send_json({"type": "user_message", "content": text})
                _drain_turn(ws)

    first, second = fake_model.requests[0], fake_model.requests[1]
    assert first["messages"][0]["role"] == "system"
    a, b = _prefix(first), _prefix(second)
    if a != b:
        i = next(i for i, (x, y) in enumerate(zip(a, b, strict=False)) if x != y)
        raise AssertionError(
            f"prefix differs at char {i}:\n{a[i - 200 : i + 200]!r}\nvs\n{b[i - 200 : i + 200]!r}"
        )

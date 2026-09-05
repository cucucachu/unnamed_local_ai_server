"""Scripted turn queue consumed by the fake OpenAI-compatible model server.

Tests build a `FakeModel`, `.queue(...)` one or more `Turn`s onto it, and pass
it to the `fake_model` fixture (see `conftest.py`). Each incoming request to
`/v1/chat/completions` pops the next turn (FIFO) and renders it as either a
non-streaming `chat.completion` object or a streamed sequence of
`chat.completion.chunk` SSE events (see `server.py`).
"""

from __future__ import annotations

import itertools
import time
from collections import deque
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.core.config import Settings


@dataclass
class TextTurn:
    """Model responds with a plain assistant text message.

    `chunk_delay_s` (M8-01): if non-zero, `_stream_turn` `await
    asyncio.sleep(chunk_delay_s)` before yielding each streamed piece —
    lets a test simulate a slow-streaming model (e.g. to send a `cancel`
    frame mid-turn with room to spare) without a real model or real wall-
    clock-heavy sleep. `0` (the default) preserves the old
    instant-streaming behavior for every other test.
    """

    text: str
    chunk_size: int = 8
    chunk_delay_s: float = 0


@dataclass
class ToolCallTurn:
    """Model responds by calling a single tool."""

    name: str
    args: dict


@dataclass
class ToolCallsTurn:
    """Model responds by calling multiple tools in a single turn."""

    calls: list[tuple[str, dict]]


Turn = TextTurn | ToolCallTurn | ToolCallsTurn


class FakeModel:
    """Scriptable stand-in for llama.cpp's OpenAI-compatible chat completions API.

    Not thread-safe by design: the fixture runs the ASGI app in-process
    against a single test at a time, so a plain `deque` and `list` suffice.
    """

    def __init__(self, model_name: str = "fake-model") -> None:
        self.model_name = model_name
        self.base_url: str = ""  # filled in by the `fake_model` fixture once bound
        self.requests: list[dict] = []
        self._turns: deque[Turn] = deque()
        self._call_ids = itertools.count(1)

    def queue(self, *turns: Turn) -> None:
        self._turns.extend(turns)

    def pop_turn(self) -> Turn:
        """Pop the next scripted turn, or raise `IndexError` if none are queued.

        An empty queue is a test/fixture-usage bug, so the server surfaces it
        as an HTTP 500 rather than hanging or guessing a response.
        """
        return self._turns.popleft()

    def next_call_id(self) -> str:
        return f"call_{next(self._call_ids)}"

    def record_request(self, body: dict) -> None:
        self.requests.append(body)

    def settings(self, **overrides: object) -> Settings:
        """Build a `Settings` with `model_base_url`/`model_name` pointed at this fake.

        Requires `.base_url` to already be set (done by the `fake_model`
        fixture once the server is bound to its ephemeral port).
        """
        from app.core.config import Settings

        overrides.setdefault("model_base_url", self.base_url)
        overrides.setdefault("model_name", self.model_name)
        overrides.setdefault("_env_file", None)
        return Settings(**overrides)


def new_completion_id() -> str:
    return f"chatcmpl-fake-{int(time.time() * 1_000_000)}"


def chunk_text(text: str, chunk_size: int) -> list[str]:
    """Split `text` into `chunk_size`-character pieces (the last may be shorter)."""
    if not text:
        return []
    return [text[i : i + chunk_size] for i in range(0, len(text), chunk_size)]


def chunk_str_min_pieces(text: str, chunk_size: int, min_pieces: int = 2) -> list[str]:
    """Like `chunk_text`, but guarantees at least `min_pieces` pieces when possible.

    Used to split serialized tool-call `arguments` JSON across multiple
    streaming chunks, per the spec's "at least 2 chunks" requirement — a
    naive fixed `chunk_size` split can collapse to 1 piece for short JSON.
    """
    pieces = chunk_text(text, chunk_size)
    if len(pieces) >= min_pieces or len(text) < min_pieces:
        return pieces or [""]
    size = max(1, len(text) // min_pieces)
    return [text[i : i + size] for i in range(0, len(text), size)]

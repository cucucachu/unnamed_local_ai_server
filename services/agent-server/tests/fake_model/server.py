"""FastAPI app implementing enough of OpenAI's `/v1/chat/completions` to fool
real client libraries (`langchain-openai` / `openai-python`), for use as a
deterministic stand-in for llama.cpp's OpenAI-compatible server in tests.

Supports both `stream: false` (a single `chat.completion` object) and
`stream: true` (Server-Sent Events of `chat.completion.chunk` objects,
terminated by `data: [DONE]\\n\\n`), driven by a scripted `FakeModel` queue of
`Turn`s (see `scripting.py`).
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncIterator
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse

from .scripting import (
    FakeModel,
    TextTurn,
    ToolCallsTurn,
    ToolCallTurn,
    Turn,
    chunk_str_min_pieces,
    chunk_text,
    new_completion_id,
)


def create_fake_model_app(fake: FakeModel) -> FastAPI:
    app = FastAPI()

    @app.post("/v1/chat/completions")
    async def chat_completions(request: Request) -> Any:
        body = await request.json()
        fake.record_request(body)

        try:
            turn = fake.pop_turn()
        except IndexError as exc:
            raise HTTPException(
                status_code=500,
                detail="FakeModel turn queue is empty — no scripted response left to serve.",
            ) from exc

        completion_id = new_completion_id()
        created = int(time.time())
        stream = bool(body.get("stream", False))
        # M8-07: llama-server honors per-request
        # `chat_template_kwargs.enable_thinking`. When the client sends
        # `false`, suppress scripted `reasoning_content` so WS tests can
        # prove thinking-off emits no `reasoning` frames. Omitted key
        # (pre-M8-07 clients / the M8-06 prototype) keeps the old "emit
        # whatever the turn scripted" behavior.
        emit_reasoning = _enable_thinking(body)

        if stream:
            return StreamingResponse(
                _stream_turn(fake, turn, completion_id, created, emit_reasoning=emit_reasoning),
                media_type="text/event-stream",
            )
        return JSONResponse(_render_turn(fake, turn, completion_id, created, emit_reasoning=emit_reasoning))

    return app


def _enable_thinking(body: dict) -> bool:
    """Whether this request should emit scripted `reasoning_content`.

    OpenAI-compat clients put `extra_body` keys on the request body itself,
    so `chat_template_kwargs.enable_thinking` lands at the top level.
    """
    kwargs = body.get("chat_template_kwargs")
    if isinstance(kwargs, dict) and "enable_thinking" in kwargs:
        return bool(kwargs["enable_thinking"])
    return True


def _choice_chunk(
    completion_id: str,
    created: int,
    model: str,
    delta: dict,
    finish_reason: str | None,
) -> dict:
    return {
        "id": completion_id,
        "object": "chat.completion.chunk",
        "created": created,
        "model": model,
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish_reason}],
    }


def _sse(chunk: dict) -> str:
    return f"data: {json.dumps(chunk)}\n\n"


async def _stream_turn(
    fake: FakeModel,
    turn: Turn,
    completion_id: str,
    created: int,
    *,
    emit_reasoning: bool = True,
) -> AsyncIterator[str]:
    model = fake.model_name

    if isinstance(turn, TextTurn):
        yield _sse(
            _choice_chunk(completion_id, created, model, {"role": "assistant", "content": ""}, None)
        )
        # M8-06: reasoning_content deltas stream *before* content deltas,
        # matching real llama-server `--reasoning-format deepseek` wire
        # order (confirmed via curl against the real model-runner — see
        # docs/TOOL_CALLING.md's M8-06 section).
        if emit_reasoning and turn.reasoning_content:
            for piece in chunk_text(turn.reasoning_content, turn.reasoning_chunk_size):
                if turn.chunk_delay_s:
                    await asyncio.sleep(turn.chunk_delay_s)
                yield _sse(
                    _choice_chunk(completion_id, created, model, {"reasoning_content": piece}, None)
                )
        for piece in chunk_text(turn.text, turn.chunk_size):
            if turn.chunk_delay_s:
                await asyncio.sleep(turn.chunk_delay_s)
            yield _sse(_choice_chunk(completion_id, created, model, {"content": piece}, None))
        yield _sse(_choice_chunk(completion_id, created, model, {}, "stop"))

    elif isinstance(turn, ToolCallTurn):
        call_id = fake.next_call_id()
        for chunk in _tool_call_stream_chunks(fake, turn.name, turn.args, index=0, call_id=call_id):
            yield _sse(_choice_chunk(completion_id, created, model, chunk, None))
        yield _sse(_choice_chunk(completion_id, created, model, {}, "tool_calls"))

    elif isinstance(turn, ToolCallsTurn):
        for index, (name, args) in enumerate(turn.calls):
            call_id = fake.next_call_id()
            for chunk in _tool_call_stream_chunks(fake, name, args, index=index, call_id=call_id):
                yield _sse(_choice_chunk(completion_id, created, model, chunk, None))
        yield _sse(_choice_chunk(completion_id, created, model, {}, "tool_calls"))

    else:  # pragma: no cover - exhaustive by construction
        raise TypeError(f"Unknown turn type: {turn!r}")

    yield "data: [DONE]\n\n"


def _tool_call_stream_chunks(
    fake: FakeModel,
    name: str,
    args: dict,
    *,
    index: int,
    call_id: str,
) -> list[dict]:
    """Build the delta payloads for one streamed tool call.

    First chunk carries the full `name` and an empty `arguments` string
    (matching real OpenAI behavior); subsequent chunks only accumulate
    `arguments` fragments and omit `id`/`type`/`name`.
    """
    chunks = [
        {
            "tool_calls": [
                {
                    "index": index,
                    "id": call_id,
                    "type": "function",
                    "function": {"name": name, "arguments": ""},
                }
            ]
        }
    ]
    args_json = json.dumps(args)
    for piece in chunk_str_min_pieces(args_json, chunk_size=8, min_pieces=2):
        chunks.append(
            {"tool_calls": [{"index": index, "function": {"arguments": piece}}]}
        )
    return chunks


def _render_turn(
    fake: FakeModel,
    turn: Turn,
    completion_id: str,
    created: int,
    *,
    emit_reasoning: bool = True,
) -> dict:
    model = fake.model_name
    base = {
        "id": completion_id,
        "object": "chat.completion",
        "created": created,
        "model": model,
    }

    if isinstance(turn, TextTurn):
        message = {"role": "assistant", "content": turn.text}
        if emit_reasoning and turn.reasoning_content:
            # M8-06: matches real llama-server's non-streamed shape, where
            # `reasoning_content` is a sibling field on `message`, not
            # nested/prefixed onto `content`.
            message["reasoning_content"] = turn.reasoning_content
        finish_reason = "stop"
    elif isinstance(turn, ToolCallTurn):
        message = {
            "role": "assistant",
            "content": None,
            "tool_calls": [_tool_call_message(fake, turn.name, turn.args)],
        }
        finish_reason = "tool_calls"
    elif isinstance(turn, ToolCallsTurn):
        message = {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                _tool_call_message(fake, name, args) for name, args in turn.calls
            ],
        }
        finish_reason = "tool_calls"
    else:  # pragma: no cover - exhaustive by construction
        raise TypeError(f"Unknown turn type: {turn!r}")

    base["choices"] = [{"index": 0, "message": message, "finish_reason": finish_reason}]
    return base


def _tool_call_message(fake: FakeModel, name: str, args: dict) -> dict:
    return {
        "id": fake.next_call_id(),
        "type": "function",
        "function": {"name": name, "arguments": json.dumps(args)},
    }

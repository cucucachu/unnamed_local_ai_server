# M2-02 — Fake model-runner test fixture (OpenAI-compatible SSE)

**Milestone**: M2 · **Size**: M · **Depends on**: M2-01 · **Blocks**: M2-03, M2-04

## Context

All deterministic agent-server tests run against a scripted fake of llama.cpp's OpenAI endpoint —
the real model is slow, nondeterministic, and holds the GPU. This fixture is the backbone of
every later backend test; get streaming tool-call chunks exactly right.

## Spec

1. **`services/agent-server/tests/fake_model/`**:
   - `server.py` — FastAPI app implementing `POST /v1/chat/completions` for both `stream: false`
     and `stream: true` (SSE: `data: {chunk}\n\n`, terminated by `data: [DONE]\n\n`).
   - `scripting.py` — a `FakeModel` class holding an ordered queue of `Turn`s. Each incoming
     request pops the next `Turn` and renders it. Turn types:
     - `TextTurn(text: str, chunk_size: int = 8)` → streamed as `delta.content` chunks,
       `finish_reason: "stop"`.
     - `ToolCallTurn(name: str, args: dict)` → streamed as OpenAI tool-call deltas: first chunk
       carries `delta.tool_calls[0]` with `index: 0`, `id: "call_<n>"`, `function.name`, then the
       JSON args string split across ≥ 2 chunks in `function.arguments`, `finish_reason: "tool_calls"`.
     - Multiple tool calls in one turn: `ToolCallsTurn([...])` with distinct `index` values.
   - Requests recorded on the instance (`fake.requests: list[dict]`) so tests can assert what
     the agent sent (messages, tools schema present, etc.).
   - If the queue is empty → respond 500 (test bug surfaced loudly).
2. **`conftest.py` fixture** `fake_model`: starts `server.py` with uvicorn on an ephemeral port
   in-process (uvicorn `Server` in a task or a thread), yields the `FakeModel` handle with
   `.base_url`, and a `settings` override pointing `model_base_url` at it. Fixture tears down
   cleanly.
3. **Self-tests** (`tests/test_fake_model.py`) prove the fake speaks the dialect by consuming it
   with the real client stack we'll use: `langchain-openai`'s `ChatOpenAI(base_url=...)`:
   - `TextTurn` → `astream` yields the text in ≥ 2 chunks, concatenating to the exact string.
   - `ToolCallTurn` → `bind_tools` + `ainvoke` returns an `AIMessage` whose
     `tool_calls == [{"name": ..., "args": ...}]`.
   - Two queued turns are consumed in order.

## Out of scope

Emulating llama.cpp quirks beyond the OpenAI dialect; latency simulation; `/health`.

## Acceptance criteria (Tier A)

- [ ] `uv run pytest tests/test_fake_model.py` green — including the ChatOpenAI-consumption
      tests (this proves the chunk format is right, not just self-consistent).
- [ ] `uv run ruff check .` green.
- [ ] Fixture leaves no dangling server/task between tests (pytest exits cleanly, no warnings
      about unclosed resources).

## Tier B

None.

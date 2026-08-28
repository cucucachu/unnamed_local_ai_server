# M2-04 — WebSocket chat endpoint (token + tool streaming)

**Milestone**: M2 · **Size**: L · **Depends on**: M2-03 · **Blocks**: M2-06, M3-01

## Context

The one transport the UI uses for chat: `WS /ws/chat/{thread_id}` relaying model tokens and
tool start/end events as JSON frames. Wire format is fixed in
[CONVENTIONS.md §6](./CONVENTIONS.md) — do not deviate; the frontend (M2-06) is built against it.

## Spec

1. **`app/api/chat_ws.py`** — `@app.websocket("/ws/chat/{thread_id}")`:
   - Accept, then loop: receive JSON frame; only `{"type":"user_message","content":str}` is
     valid — anything else → `error` frame + close 1008.
   - Per-thread serialization: module-level `dict[str, asyncio.Lock]` keyed by thread_id
     (locks created on demand); hold the lock for the duration of a turn.
   - Turn execution: send `turn_start`, then run
     `agent.astream_events({"messages": [HumanMessage(content)]},
     config={"configurable": {"thread_id": thread_id}})` and map events:
     - `on_chat_model_stream` → `{"type":"token","content": chunk.text}` (skip empty chunks and
       chunks that only carry tool-call deltas).
     - `on_tool_start` → `tool_start` frame; `category` per CONVENTIONS §6 mapping; `args`
       values str-truncated to 500 chars.
     - `on_tool_end` → `tool_end` frame, `status:"success"`, `result_preview` = str(output)
       truncated to 2000 chars.
     - Tool exception → `tool_end` with `status:"error"`, `result_preview` = repr(exc) (the
       agent loop itself continues; deepagents feeds errors back to the model).
     - Completion → `turn_end`.
   - Unhandled exception during a turn → `{"type":"error","message":...}`, close 1011. Client
     disconnect mid-turn → cancel the turn task, release the lock (agent state keeps whatever
     the checkpointer captured; acceptable for v1).
   - Keep frames pure-JSON-serializable; one JSON object per text frame.
2. Mount under `/ws` (not `/api`) to match Caddy routing.
3. **Tests** (fake model + `httpx`/`starlette` WebSocket test client):
   - `test_plain_turn`: `TextTurn("hello world")` → frames are exactly `turn_start`,
     ≥2 `token`s concatenating to `"hello world"`, `turn_end`.
   - `test_tool_turn`: `ToolCallTurn(write_file ...)` + `TextTurn("done")` → frame sequence
     contains `tool_start`(name=write_file, category=file) before `tool_end`(success), then
     tokens, then `turn_end`; file exists in tmp workspace.
   - `test_invalid_frame`: garbage JSON → `error` frame, close code 1008.
   - `test_two_turns_same_socket`: two `user_message`s sequentially → two full turn cycles,
     memory retained (fake asserts request 2 contains turn-1 messages).
   - `test_concurrent_turns_serialized`: two sockets on the same thread_id, send simultaneously
     → both complete; fake-model request log shows no interleaving (request 2's messages
     include turn 1's exchange).

## Out of scope

Frontend client; thread title auto-set (M3-02); persistence across restarts (M3-01);
stop/regenerate controls (not v1).

## Acceptance criteria (Tier A)

- [ ] All five tests green; `uv run ruff check .` green.
- [ ] Manual smoke through the full proxy chain with the **real model**: with the stack up, run
      a 10-line `scripts/ws_smoke.py` (committed; uses `websockets` lib) that connects to
      `ws://localhost/ws/chat/smoke-1`, sends "Say exactly: PONG", and prints frames — output
      shows `turn_start`, tokens, `turn_end`. (Loose assert: ≥1 token frame and a `turn_end`.)

## Tier B

None.

# M3-02 — Threads REST (list/create/history/delete) + title auto-set

**Milestone**: M3 · **Size**: M · **Depends on**: M3-01 · **Blocks**: M3-04

## Context

REST for thread management and history hydration on page load / thread switch (the WS only
carries live turns). Contract fixed in [CONVENTIONS.md §5](./CONVENTIONS.md) "Threads".

## Spec

1. **`app/api/chat.py`** implementing exactly the CONVENTIONS §5 Threads contract:
   - `POST /api/threads`: insert row (title from body or default), return DTO, 201.
   - `GET /api/threads`: all rows, `updated_at` desc.
   - `GET /api/threads/{id}/messages`: load state via
     `agent.aget_state({"configurable": {"thread_id": str(id)}})`; normalize
     `state.values["messages"]` to the DTO: `HumanMessage`→`user`; `AIMessage`→`assistant`
     (include `tool_calls` list if present, content may be empty string); `ToolMessage`→`tool`
     with `tool_name`. Skip system messages. Unknown thread id (no row) → 404; row exists but
     no checkpoint yet → `[]`.
   - `DELETE /api/threads/{id}`: delete row; call `checkpointer.adelete_thread(str(id))` (if
     the installed langgraph version lacks it, delete from the checkpointer tables directly by
     `thread_id` — comment which path was taken); 204; idempotent (missing row → 204).
   - DB access: raw SQL via the existing psycopg pool (no ORM — this is 4 queries).
2. **WS integration** (touch `chat_ws.py`):
   - On each `user_message` for a thread whose row's title is still `'New chat'`, set title =
     first 60 chars of the message (single-line, ellipsis if truncated).
   - On every completed turn, bump `updated_at = now()`.
   - WS connections for a thread_id with no `threads` row: auto-insert the row (keeps
     `ws_smoke.py` and old gate scripts working).
3. **Tests** (fake model, MemorySaver, threads table via… note: unit tests have no Postgres).
   Split the data layer behind a tiny `ThreadStore` protocol with two impls: `PgThreadStore`
   (prod) and `InMemoryThreadStore` (tests, dict-backed). Unit tests cover the REST layer with
   the in-memory store: create/list ordering/delete idempotence/404s, title auto-set + bump via
   two WS turns, history normalization (drive one tool-call turn through the fake model, then
   GET messages → assert user/assistant/tool rows). Extend the `-m integration` module with a
   `PgThreadStore` round-trip test.

## Out of scope

Frontend (M3-04); pagination (single user, not v1); message editing/deletion.

## Acceptance criteria (Tier A)

- [ ] Unit + integration tests green; ruff green.
- [ ] Manual curl pass against the live stack (through Caddy): create → appears in list → run a
      `ws_smoke.py` turn on it → title changed + `updated_at` bumped → GET messages shows the
      turn → DELETE → gone from list, messages 404.

## Tier B

None.

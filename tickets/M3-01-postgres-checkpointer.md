# M3-01 — Postgres + AsyncPostgresSaver + threads table

**Milestone**: M3 · **Size**: M · **Depends on**: M2-04 · **Blocks**: M3-02

## Context

Swap the in-memory saver for durable LangGraph checkpoints in Postgres, plus our own small
`threads` metadata table (the checkpointer stores state, not titles/ordering). PLAN.md
P3-5/P3-10.

## Spec

1. **compose service**:

```yaml
postgres:
  image: postgres:17
  restart: unless-stopped
  networks: [homeai-net]
  environment:
    POSTGRES_USER: ${POSTGRES_USER}
    POSTGRES_PASSWORD: ${POSTGRES_PASSWORD}
    POSTGRES_DB: ${POSTGRES_DB}
  volumes:
    - pgdata:/var/lib/postgresql/data
  healthcheck:
    test: ["CMD-SHELL", "pg_isready -U ${POSTGRES_USER} -d ${POSTGRES_DB}"]
    interval: 5s
    timeout: 3s
    retries: 20
```

   Named volume `pgdata` under top-level `volumes:`. agent-server gets
   `depends_on: { postgres: { condition: service_healthy } }` and the `POSTGRES_*` env vars;
   `Settings` gains `postgres_dsn` property
   (`postgresql://user:pass@postgres:5432/db`).
2. **`app/db/checkpointer.py`**:
   - Lifespan-managed `psycopg_pool.AsyncConnectionPool(dsn, kwargs={"autocommit": True,
     "prepare_threshold": 0, "row_factory": dict_row})` (these kwargs are required — see
     langgraph checkpoint-postgres README).
   - `AsyncPostgresSaver(pool)`; `await saver.setup()` once at startup (runs its own
     migrations).
   - Our DDL, executed after `setup()` (idempotent):

```sql
CREATE TABLE IF NOT EXISTS threads (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  title TEXT NOT NULL DEFAULT 'New chat',
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

3. Replace `MemorySaver` in the app lifespan with the saver; agent construction unchanged
   otherwise. Tests keep using `MemorySaver` via the `create_app()` override (fast, no DB), so
   **add one integration test module** `tests/test_checkpointer_pg.py` marked
   `@pytest.mark.integration` that runs only when `TEST_PG_DSN` env is set: builds the agent
   with a real `AsyncPostgresSaver` against compose Postgres, runs two fake-model turns on one
   thread, tears the saver down, rebuilds it, asserts the third request still carries prior
   messages. Add a `Makefile`/`justfile`-free instruction in the module docstring:
   `TEST_PG_DSN=... uv run pytest -m integration` with Postgres up and port temporarily
   published via `docker compose run` helper or `docker compose exec` psql network — simplest:
   run the test **inside** the agent-server container:
   `docker compose exec agent-server uv run pytest -m integration` with `TEST_PG_DSN` set from
   compose env. Spec the pytest marker in `pyproject.toml` so unmarked runs skip it.

## Out of scope

Threads REST (M3-02); title auto-set (M3-02); checkpoint pruning/retention (not v1).

## Acceptance criteria (Tier A)

- [ ] `uv run pytest` (unit, fake saver) green and unchanged in runtime.
- [ ] `docker compose up -d` → agent-server healthy; Postgres contains checkpointer tables +
      `threads` (`docker compose exec postgres psql -U $POSTGRES_USER -d $POSTGRES_DB -c '\dt'`
      lists `checkpoints`/`checkpoint_*` and `threads`).
- [ ] Integration test green: `docker compose exec agent-server uv run pytest -m integration`.
- [ ] **Restart persistence**: run `scripts/ws_smoke.py` on thread `pg-1` ("My name is Bob."),
      `docker compose restart agent-server`, then ask "What is my name?" on the same thread —
      response mentions Bob. Automate as `scripts/e2e/persistence_smoke.sh`.

## Tier B

None.

# M2-01 — agent-server skeleton + Dockerfile + compose

**Milestone**: M2 · **Size**: M · **Depends on**: M0-01 · **Blocks**: M2-02, M3-03

## Context

FastAPI service that will host the agent, chat WS, files and media APIs. This ticket is
structure only: config, health, container, compose wiring, test harness. PLAN.md P3-1.
Critical invariant: app code lives at `/app`, workspace mounts at `/data/workspace` — disjoint
paths (the isolation story depends on it).

## Spec

1. **`services/agent-server/`** uv project: Python 3.12, deps per CONVENTIONS.md §4 (add
   deepagents etc. now so the lockfile is stable, even though M2-03 uses them first). Dev deps:
   `pytest`, `pytest-asyncio`, `httpx`, `ruff`.
2. Layout (PLAN.md "Repository layout"):

```
services/agent-server/
  Dockerfile
  pyproject.toml
  uv.lock
  app/
    __init__.py
    main.py            # FastAPI app factory `create_app()`, lifespan hook (empty for now)
    core/config.py     # pydantic-settings Settings: model_base_url, model_name,
                       # exec_manager_url, workspace_root (default /data/workspace),
                       # postgres_* (unused until M3-01), exec_default_timeout_s
    api/health.py      # GET /api/health -> {"status":"ok"}
    agent/__init__.py
    db/__init__.py
  tests/
    conftest.py        # app fixture with httpx.AsyncClient (ASGITransport)
    test_health.py
```

3. All routes mounted under `/api` (matches Caddy routing). `create_app()` takes an optional
   `Settings` override for tests.
4. **Dockerfile**: `python:3.12-slim`; install uv (copy from `ghcr.io/astral-sh/uv` distroless);
   `WORKDIR /app`; `uv sync --frozen --no-dev`; copy `app/`; `CMD ["uv", "run", "uvicorn",
   "app.main:app", "--host", "0.0.0.0", "--port", "8000"]` where `app.main:app = create_app()`.
   Layer-cache friendly: copy `pyproject.toml`+`uv.lock` before source.
5. **compose service**:

```yaml
agent-server:
  build: ./services/agent-server
  restart: unless-stopped
  networks: [homeai-net]
  user: "${HOMEAI_UID}:${HOMEAI_GID}"
  volumes:
    - ${WORKSPACE_DIR}:/data/workspace
  environment:
    MODEL_BASE_URL: ${MODEL_BASE_URL}
    MODEL_NAME: ${MODEL_NAME}
    EXEC_MANAGER_URL: ${EXEC_MANAGER_URL}
    EXEC_DEFAULT_TIMEOUT_S: ${EXEC_DEFAULT_TIMEOUT_S}
```

   No ports published.

## Out of scope

Agent construction, WS, files/media/threads routes, Postgres.

## Acceptance criteria (Tier A)

- [ ] `uv run ruff check . && uv run pytest` green in `services/agent-server/` (health test:
      200 + exact body).
- [ ] `docker compose build agent-server && docker compose up -d agent-server caddy` →
      `curl -s http://localhost/api/health` (through Caddy) returns `{"status":"ok"}` — this
      also closes M0-03's 502.
- [ ] Path disjointness: `docker compose exec agent-server sh -c 'ls /app && ls /data/workspace'`
      works and `/app` is not under `/data/workspace`; a file touched in the host workspace
      appears in `/data/workspace` inside the container.
- [ ] Container runs as `${HOMEAI_UID}` (`docker compose exec agent-server id -u`).

## Tier B

None.

# Shared Conventions & Contracts

Every ticket in this folder assumes the specs below. Tickets reference this file instead of
restating it. **If a ticket appears to conflict with this file, this file wins** — flag the
conflict in your PR description.

Referenced docs: [`../README.md`](../README.md) (what/why), [`../PLAN.md`](../PLAN.md)
(architecture), [`BACKLOG.md`](./BACKLOG.md) (ordering & gates).

---

## 1. Execution environment for coding agents

All tickets are executed **directly on the target Linux host** (Ryzen AI 9 HX 370, 96 GB RAM,
Radeon 890M iGPU, native Linux, Docker Engine + Compose v2 installed). This means acceptance
criteria may — and should — use real `docker compose`, the real GPU, and the real model unless
the ticket says otherwise. Only phone/LAN-device checks are deferred to the human host checklist
(“Tier B”).

- Repo root on host: wherever the repo is cloned; all paths in tickets are repo-relative unless
  they start with `/`.
- Host workspace directory: `/srv/homeai/workspace` (created by `M0-02`).

## 2. Service topology (fixed)

| Compose service      | Image source                          | Internal port | Published port |
|----------------------|---------------------------------------|--------------|----------------|
| `caddy`              | `infra/caddy/Dockerfile`              | 80           | **80 (only published port in the stack)** |
| `agent-server`       | `services/agent-server/Dockerfile`    | 8000         | none           |
| `model-runner`       | `services/model-runner/Dockerfile`    | 8080         | none           |
| `code-exec-manager`  | `services/code-exec-manager/Dockerfile` | 8090       | none           |
| `postgres`           | `postgres:17` (official)              | 5432         | none           |

- Compose project name: `homeai`. Docker network: `homeai-net` (bridge, defined in root
  `docker-compose.yml`).
- Exec containers (created by code-exec-manager, **not** compose-managed): image
  `homeai-exec-toolbox:latest`, name `homeai-exec-{session_id}`, `network_mode: none`.
- Only `code-exec-manager` mounts `/var/run/docker.sock`. Nothing else, ever.

## 3. Environment variables (`.env`, template in `.env.example`)

| Variable | Default | Consumed by |
|---|---|---|
| `HOMEAI_UID` / `HOMEAI_GID` | `1000` / `1000` | agent-server `user:`, exec containers, workspace chown |
| `WORKSPACE_DIR` | `/srv/homeai/workspace` | compose bind mounts; code-exec-manager (as **host** path for exec-container binds) |
| `MODEL_FILE` | `gemma-4-26B-A4B-it-Q4_K_M.gguf` | model-runner |
| `MODEL_CTX_SIZE` | `32768` | model-runner |
| `MODEL_EXTRA_ARGS` | *(empty)* | model-runner (appended to llama-server command) |
| `MODEL_NAME` | `gemma-4-26b-a4b-it` | agent-server (`model=` field for the OpenAI-compat client) |
| `RENDER_GID` / `VIDEO_GID` | *(set from `getent group render/video`)* | model-runner `group_add` |
| `POSTGRES_USER` | `homeai` | postgres, agent-server |
| `POSTGRES_PASSWORD` | *(required, no default)* | postgres, agent-server |
| `POSTGRES_DB` | `homeai` | postgres, agent-server |
| `MODEL_BASE_URL` | `http://model-runner:8080/v1` | agent-server |
| `EXEC_MANAGER_URL` | `http://code-exec-manager:8090` | agent-server |
| `EXEC_IDLE_MINUTES` | `30` | code-exec-manager |
| `EXEC_DEFAULT_TIMEOUT_S` | `120` | code-exec-manager, `execute_code` tool |
| `LAN_SUBNET` | `192.168.1.0/24` | `setup-ufw.sh` |
| `EXPO_PUBLIC_API_HOST` | `http://homeai.local` | frontend (native only; web uses same-origin) |

Agent-server reads env via `pydantic-settings` with prefix-less names above
(`app/core/config.py`, class `Settings`).

## 4. Toolchain pins

- **Python**: 3.12, managed with **uv** (`pyproject.toml` + committed `uv.lock` per service).
  Lint/format: `ruff` (defaults + line-length 100). Tests: `pytest` + `pytest-asyncio`
  (`asyncio_mode = "auto"`) + `httpx` for API tests.
- **Key Python deps** (agent-server): `fastapi`, `uvicorn[standard]`, `pydantic-settings`,
  `httpx`, `deepagents>=0.7.10,<0.8`, `langchain-openai` (v1.x line, per deepagents constraint
  `langchain>=1.3.18,<2`), `langgraph-checkpoint-postgres`, `psycopg[binary]`, `psycopg_pool`.
  Add with `uv add` (latest satisfying versions) and commit `uv.lock`.
- **code-exec-manager deps**: `fastapi`, `uvicorn[standard]`, `pydantic-settings`, `docker`.
- **Node**: 22 LTS. Frontend: latest stable Expo SDK via `npx create-expo-app@latest`
  (TypeScript, Expo Router). Commit the lockfile (`package-lock.json`).
- **llama.cpp image**: `ghcr.io/ggml-org/llama.cpp:server-vulkan`. Must be a build **newer than
  April 2026** (Gemma 4 chat-template fix). On first build, record the resolved image digest in
  `.env.example` as a comment and pin it in the Dockerfile `FROM` line by digest.
- **Model**: HF repo `ggml-org/gemma-4-26B-A4B-it-GGUF`, file
  `gemma-4-26B-A4B-it-Q4_K_M.gguf` (~16.8 GB). Recommended sampling (from model card):
  `--temp 1.0 --top-p 0.95 --top-k 64`.
- **Base images**: `python:3.12-slim` (Python services), `caddy:2-alpine` (proxy stage),
  `node:22-alpine` (frontend build stage), `ubuntu:24.04` (exec toolbox), `postgres:17`.

## 5. HTTP API contract (agent-server, all under `/api`)

All JSON. Errors: `{"detail": "<human readable>"}` with appropriate 4xx/5xx status.
All `path` parameters are **workspace-relative POSIX paths** (`""` = workspace root). Any path
that resolves outside the workspace root → `400` (see §8).

### Health
- `GET /api/health` → `200 {"status":"ok"}`

### Threads
- `POST /api/threads` body `{"title": "optional string"}` →
  `201 {"id": "<uuid>", "title": "New chat", "created_at": iso8601, "updated_at": iso8601}`
- `GET /api/threads` → `200 [{thread}, ...]` ordered by `updated_at` desc
- `GET /api/threads/{id}/messages` → `200 [{"id": str, "role": "user"|"assistant"|"tool",
  "content": str, "tool_name": str|null, "tool_calls": [{"id","name","args"}]|null}, ...]`
  (normalized from the LangGraph checkpoint; `tool` rows carry the tool result text)
- `DELETE /api/threads/{id}` → `204` (deletes row + checkpointer state for the thread)

### Files
- `GET /api/files?path=<dir>` → `200 {"path": str, "entries": [{"name": str, "path": str,
  "type": "file"|"dir", "size": int, "mtime": iso8601, "mime": str|null}]}` sorted dirs-first,
  then case-insensitive by name. `404` if dir missing.
- `POST /api/files/upload` — multipart form: field `path` (target **dir**), field `file`
  (binary, may repeat) → `201 {"uploaded": ["rel/path", ...]}`. Overwrites existing files.
- `GET /api/files/download?path=<file>` → `200` binary, `Content-Disposition: attachment`.
- `POST /api/files/mkdir` body `{"path": str}` → `201`. Parents created (`mkdir -p` semantics).
- `POST /api/files/move` body `{"src": str, "dst": str}` → `200`. Rename == move. `409` if dst exists.
- `POST /api/files/copy` body `{"src": str, "dst": str}` → `200`. Dirs copied recursively. `409` if dst exists.
- `DELETE /api/files?path=<p>` → `204`. Dirs deleted recursively.

### Media
- `GET /api/media/stream?path=<file>` — full spec in ticket `M5-01`. `Range`-aware,
  `206 Partial Content`, `Accept-Ranges: bytes`.

## 6. WebSocket chat contract (`/ws/chat/{thread_id}`)

One JSON object per text frame. Connection stays open across turns; turns for one thread are
serialized server-side (per-thread `asyncio.Lock`).

**Client → server**

```json
{"type": "user_message", "content": "string"}
```

**Server → client** (in order within a turn):

```json
{"type": "turn_start"}
{"type": "token", "content": "str"}                      // one per streamed model token chunk
{"type": "tool_start", "tool_call_id": "str", "name": "str",
 "category": "file"|"exec"|"plan"|"other", "args": {}}    // args truncated to 500 chars/value
{"type": "tool_end", "tool_call_id": "str", "name": "str",
 "status": "success"|"error", "result_preview": "str"}    // truncated to 2000 chars
{"type": "turn_end"}
{"type": "error", "message": "str"}                       // then normal close, code 1011
```

Category mapping by tool name: `ls|read_file|write_file|edit_file|glob|grep|delete` → `file`;
`execute_code` → `exec`; `write_todos|task` → `plan`; anything else → `other`.

## 7. code-exec-manager API contract (internal, port 8090)

- `POST /sessions/{session_id}/ensure` → `200 {"container_id": str, "created": bool}`.
  `session_id` must match `^[a-zA-Z0-9_-]{1,64}$` (thread UUIDs qualify) → else `422`.
- `POST /sessions/{session_id}/execute` body
  `{"command": str, "timeout_seconds": int = EXEC_DEFAULT_TIMEOUT_S}` →
  `200 {"stdout": str, "stderr": str, "exit_code": int, "timed_out": bool, "duration_ms": int,
  "truncated": bool}` (stdout/stderr each truncated to 200 000 bytes).
  `404` if session doesn't exist (caller must `ensure` first).
- `DELETE /sessions/{session_id}` → `204` (stop + remove container; idempotent).
- `GET /sessions` → `200 [{"session_id": str, "container_id": str, "last_used": iso8601}]`.

Exec-container hardening spec (single source of truth, used by `M4-02` and verified by `M4-05`):
`network_mode="none"`, `cap_drop=["ALL"]`, `security_opt=["no-new-privileges"]`,
`read_only=True`, `tmpfs={"/tmp": "size=512m", "/home/homeai": "size=64m"}`, `mem_limit="4g"`,
`nano_cpus=4_000_000_000` (4 CPUs), `user=f"{HOMEAI_UID}:{HOMEAI_GID}"`, `pids_limit=512`,
single bind mount `WORKSPACE_DIR (host path) -> /workspace (rw)`, command `sleep infinity`,
labels `{"homeai.exec": "1", "homeai.session": session_id}`. Nothing else mounted. No env
secrets passed in.

## 8. Path-traversal guard (used by files API, media API)

```python
def resolve_workspace_path(rel: str) -> Path:
    root = Path("/data/workspace").resolve()
    p = (root / rel).resolve()          # resolves symlinks and ".."
    if p != root and root not in p.parents:
        raise HTTPException(400, "path escapes workspace")
    return p
```

Tests must cover: `../x`, absolute `/etc/passwd`, nested `a/../../x`, and a symlink inside the
workspace pointing outside it (the resolved target must be rejected).

## 9. Testing & definition of done (every ticket)

1. **Tier A (automated, required to close the ticket)** — run on the host:
   - Python services: `uv run ruff check . && uv run pytest` green inside the service dir.
   - Compose validity whenever compose is touched: `docker compose config -q`.
   - Any e2e script named in the ticket exits `0`.
2. **Tier B (human host-checklist)** — if the ticket lists Tier B items, append them to
   `docs/HOST-CHECKS.md` under the ticket's milestone heading. Do **not** attempt them yourself
   (they need a phone or LAN device).
3. Deterministic agent-server tests use the **fake model runner** fixture (`M2-02`), never the
   real model. Tests that intentionally hit the real model live in `scripts/e2e/` and are
   invoked only by gate tickets.
4. No new published ports, no new docker.sock mounts, no auth added (v1 is trusted-LAN by
   design — see README.md).
5. Commit messages: `M2-04: short imperative summary`.

## 10. Out of scope for v1 (do not build, even if tempting)

TLS/HTTPS, any auth, docker-socket-proxy, transcoding, EAS builds, multi-user, GPU queueing,
runtime pip/npm in exec containers, exposing anything to the internet. These are Phase-6
fast-follows documented in PLAN.md.

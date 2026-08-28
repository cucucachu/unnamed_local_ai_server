# M4-03 — Idle reaper + compose wiring (sole socket holder)

**Milestone**: M4 · **Size**: M · **Depends on**: M4-02 · **Blocks**: M4-04, M4-05

## Context

Session containers must not accumulate forever (PLAN.md P2-3), and the manager joins the stack
as the only docker.sock holder — including surviving its own restarts with orphaned exec
containers present.

## Spec

1. **Reaper** (`app/reaper.py`): asyncio background task started in the FastAPI lifespan,
   every 60 s:
   - List containers with label `homeai.exec=1` (including stopped).
   - For each: idle time = now − `last_used.get(session_id)`; if the session is unknown to
     this process (manager restarted), **adopt it**: seed `last_used` from the container's
     `State.StartedAt` on first sight.
   - Idle > `EXEC_IDLE_MINUTES` → stop+remove, log one line.
   - Errors logged, never crash the loop.
2. **compose service**:

```yaml
code-exec-manager:
  build: ./services/code-exec-manager
  restart: unless-stopped
  networks: [homeai-net]
  volumes:
    - /var/run/docker.sock:/var/run/docker.sock
  environment:
    WORKSPACE_DIR: ${WORKSPACE_DIR}
    HOMEAI_UID: ${HOMEAI_UID}
    HOMEAI_GID: ${HOMEAI_GID}
    EXEC_IDLE_MINUTES: ${EXEC_IDLE_MINUTES}
    EXEC_DEFAULT_TIMEOUT_S: ${EXEC_DEFAULT_TIMEOUT_S}
```

   No ports. Note it runs as root (needs the socket) — acceptable v1, PLAN.md documents the
   socket-proxy fast-follow.
3. **Socket-exclusivity check as a test**: add
   `scripts/check_socket_exclusivity.sh` — parses `docker compose config --format json` (jq)
   and fails if any service other than `code-exec-manager` mounts a path containing
   `docker.sock`. (Reused by M4-05 and gates.)
4. Unit tests for the reaper with a faked docker client + injectable clock: idle container
   reaped, active kept, unknown container adopted-then-reaped after idle window, listing error
   doesn't kill the loop (loop-body function is factored to be testable without the 60 s
   timer).

## Out of scope

Agent tool (M4-04); resource-usage metrics.

## Acceptance criteria (Tier A)

- [ ] Unit tests + ruff green.
- [ ] `docker compose up -d code-exec-manager` healthy; `smoke.sh` (M4-02) passes against it
      over the compose network (run curls from a one-off container on `homeai-net`).
- [ ] Reaper live-fire with `EXEC_IDLE_MINUTES=1` (env override): create a session, wait
      ≤ 3 min, container is gone; restart the manager between create and reap to prove
      adoption.
- [ ] `scripts/check_socket_exclusivity.sh` exits 0; manually adding a socket mount to another
      service makes it exit 1 (verify once, revert).

## Tier B

None.

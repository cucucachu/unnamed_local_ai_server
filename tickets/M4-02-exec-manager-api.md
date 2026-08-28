# M4-02 — code-exec-manager (ensure/execute/delete)

**Milestone**: M4 · **Size**: L · **Depends on**: M4-01 · **Blocks**: M4-03

## Context

The only service allowed to touch the Docker socket. It exposes the CONVENTIONS §7 API and
hardcodes every container parameter — callers send a command string, never a spec (PLAN.md
"Isolation boundary"). Session = chat thread; containers are reused per session.

## Spec

1. **`services/code-exec-manager/`** uv project (deps: `fastapi`, `uvicorn[standard]`,
   `pydantic-settings`, `docker`; dev: `pytest`, `httpx`, `ruff`).
2. `app/main.py` + `app/core/config.py` (Settings: `workspace_host_dir` — the **host** path
   from `WORKSPACE_DIR`; `homeai_uid`, `homeai_gid`, `exec_idle_minutes`,
   `exec_default_timeout_s`, `toolbox_image` default `homeai-exec-toolbox:latest`).
3. **`app/sessions.py`** — the container lifecycle, exactly the CONVENTIONS §7 hardening spec:
   - `ensure(session_id)`: validate id regex → find container by name
     `homeai-exec-{session_id}`; if absent/stopped, create+start with the §7 spec (**the bind
     mount source must be the host path**, since dockerd interprets it — never the manager's
     own mount view). Docker SDK call must set every §7 field explicitly. Return
     `{container_id, created}`.
   - `execute(session_id, command, timeout_seconds)`:
     - Wrap: `["timeout", "--signal=TERM", "--kill-after=5", f"{timeout_seconds}s",
       "bash", "-lc", command]` via `container.exec_run(..., demux=True, user=...)` — GNU
       timeout inside handles the wall clock; an outer `asyncio.wait_for` at
       `timeout_seconds + 15` guards against docker-API hangs (on outer timeout: kill+remove
       the container, return `timed_out=True, exit_code=-1`).
     - GNU-timeout expiry surfaces as exit code 124 → map to `timed_out=True` (keep 124 as
       `exit_code`).
     - Truncate stdout/stderr to 200 000 bytes each, set `truncated` flag; decode utf-8 with
       `errors="replace"`; record `duration_ms`.
     - Blocking docker SDK calls run via `anyio.to_thread.run_sync` (the SDK is sync).
   - `remove(session_id)`: stop (t=5) + remove; idempotent.
   - `last_used[session_id]` timestamp updated on ensure/execute (module state; M4-03 reaps).
4. **`app/api.py`**: the four §7 endpoints mapping directly onto `sessions.py`. `execute` on a
   nonexistent session → 404 (no auto-ensure — the agent tool owns that policy).
5. **Dockerfile**: `python:3.12-slim` + uv, same pattern as agent-server. (Compose wiring is
   M4-03 — for this ticket run it ad hoc, see AC.)
6. **Tests** — two layers:
   - Unit (`pytest`, docker SDK faked with a stub client class): id validation, §7 spec dict
     asserted field-by-field (this is the security test — `network_mode=="none"`,
     `cap_drop==["ALL"]`, `read_only is True`, mounts exactly one bind, etc.), truncation,
     124→timed_out mapping.
   - Integration (`-m integration`, real Docker + real toolbox image): ensure→created true,
     ensure again→created false, execute `echo hi` → stdout `hi\n` exit 0, execute
     `sleep 30` with `timeout_seconds=2` → timed_out true in < 10 s, file write in
     `/workspace` visible at the host path, delete → container gone (`docker ps -a` clean).

## Out of scope

Idle reaper + compose service (M4-03); agent tool (M4-04); isolation suite (M4-05).

## Acceptance criteria (Tier A)

- [ ] Unit + integration tests green (`uv run pytest` and `uv run pytest -m integration` run
      on the host where Docker + toolbox image exist); ruff green.
- [ ] Ad-hoc run works end to end:
      `docker build -t homeai-exec-manager services/code-exec-manager && docker run --rm -p 127.0.0.1:8090:8090 -v /var/run/docker.sock:/var/run/docker.sock -e WORKSPACE_DIR=/srv/homeai/workspace ... homeai-exec-manager`
      then the curl sequence: ensure → execute (`python3 -c 'print(6*7)'` → stdout `42`) →
      write file → verify on host → delete. Save this sequence as
      `services/code-exec-manager/smoke.sh`.
- [ ] Grep check: no endpoint accepts image names, mount specs, network modes, user, or any
      container parameter from the request body.

## Tier B

None.

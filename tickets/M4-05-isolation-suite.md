# M4-05 — Isolation verification suite (scripted)

**Milestone**: M4 · **Size**: M · **Depends on**: M4-03 · **Blocks**: M4-07

## Context

The security promise of the product ("safe to let it run code", PRODUCT.md) verified as a
repeatable script, not a one-time manual check. Covers PLAN.md P2-4 and the isolation half of
P3-11. Runs inside a live exec container and against the compose config.

## Spec

**`scripts/verify_isolation.sh`** — creates a session via the manager API
(`isolation-suite`), then drives checks **through the manager's own `execute` endpoint** (so we
verify what agent-launched code can actually do), asserting each expectation; any failure →
red, exit 1; ends by deleting the session. Checks:

| # | Command inside exec container | Expect |
|---|------------------------------|--------|
| 1 | `cat /proc/net/route; ls /sys/class/net` | only `lo`; no default route |
| 2 | `curl -m 3 http://agent-server:8000/api/health` | DNS/connect failure (nonzero) |
| 3 | `curl -m 3 http://192.168.1.1` (and the host's LAN IP) | connect failure |
| 4 | `ls /var/run/docker.sock /run/docker.sock` | not found |
| 5 | `ls /app /data` | not found (no agent-server code/config paths) |
| 6 | `env` | no `POSTGRES_*`, no `MODEL_*`, no secrets (grep) |
| 7 | `touch /forbidden` | fails (read-only rootfs) |
| 8 | `touch /tmp/x && touch $HOME/x` | succeed (tmpfs) |
| 9 | `touch /workspace/isolation-ok && rm /workspace/isolation-ok` | succeeds (rw workspace) |
| 10 | `id -u` | `${HOMEAI_UID}`, not 0 |
| 11 | `grep CapEff /proc/self/status` | `0000000000000000` (all caps dropped) |
| 12 | `python3 -c "import socket; socket.create_connection(('1.1.1.1',80),3)"` | raises |
| 13 | `nproc` / read `/sys/fs/cgroup/memory.max` | CPU ≤ 4, memory == 4g (limits applied) |
| 14 | `mount` | exactly one non-tmpfs writable mount: `/workspace` |

Plus stack-level checks in the same script:
- `scripts/check_socket_exclusivity.sh` (M4-03) passes.
- `docker inspect` of the exec container asserts: `NetworkMode=none`, `ReadonlyRootfs=true`,
  `CapDrop=[ALL]`, `Privileged=false`, exactly one bind mount and its source ==
  `$WORKSPACE_DIR`.
- `docker compose config` shows no service other than caddy publishing ports.

Document the suite (one section in `docs/ARCHITECTURE.md`: "Isolation verification — run
`scripts/verify_isolation.sh` after any change to code-exec-manager or the toolbox image").

## Out of scope

Fixing findings beyond code-exec-manager parameter bugs (if a check fails because the §7 spec
was implemented wrong, fix in M4-02's code); kernel-level hardening (seccomp/AppArmor custom
profiles — Docker defaults suffice for v1, per PLAN.md).

## Acceptance criteria (Tier A)

- [ ] `scripts/verify_isolation.sh` green on the host, all 14 + 3 stack checks.
- [ ] Deliberate-failure verification (once, then revert): loosen one parameter in
      `sessions.py` (e.g. drop `network_mode="none"`) → suite goes red on the right check.
      Note the check number in the PR description.

## Tier B (append to docs/HOST-CHECKS.md under M4)

- [ ] PM reads the suite output and countersigns the isolation section.

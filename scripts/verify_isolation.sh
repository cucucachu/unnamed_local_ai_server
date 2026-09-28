#!/usr/bin/env bash
# verify_isolation.sh — M4-05: scripted isolation verification suite.
#
# The product's core safety promise ("safe to let it run code") verified as
# a repeatable script instead of a one-time manual check. Most checks drive
# commands INSIDE a live exec container THROUGH the manager's own
# `POST /sessions/{id}/execute` endpoint — never via `docker exec` straight
# into the exec container, which would bypass exactly what's being tested
# (an agent can only ever reach the container through that same endpoint,
# so that's the only path this suite is willing to trust). Checks 15-17
# inspect the compose stack itself directly via `docker`, since there's no
# "manager endpoint" equivalent for socket-exclusivity, the exec container's
# own `docker inspect`, or the compose port-publishing policy.
#
# Verifies `services/code-exec-manager/app/sessions.py`'s `build_run_kwargs`
# §7 hardening spec: `network_mode="none"`, `cap_drop=["ALL"]`,
# `security_opt=["no-new-privileges"]`, `read_only=True`, tmpfs `/tmp` +
# `/home/homeai`, `mem_limit="4g"`, `nano_cpus=4_000_000_000`,
# `pids_limit=512` — and, since M11-03 (docs/PLATFORM.md §6), that the
# container runs as the delegation's user (`<user uid>:<personal space
# gid>`, `group_add` = their other spaces' gids) with exactly one bind per
# space they belong to: `/files/personal` and `/files/spaces/<slug>`,
# read-only for a space they only view.
#
# ---- Two throwaway users, real delegations --------------------------------
#
# Every session call needs `Authorization: Bearer <delegation>` whose `thr`
# is the session id. The suite creates two `e2e-iso-*` users with the
# recovery CLI (`scripts/e2e/lib/auth.sh`; never completes bootstrap) and
# a shared space `e2e-iso-<hex>` that A owns and B views. Delegations are
# minted the way agent-server does it: the session cookie is exchanged at
# the platform's `/internal/auth/verify` for an identity token, which
# `/internal/delegations` (service token `PLATFORM_AGENT_TOKEN`, read from
# `.env` and passed to the runner by environment, never printed) turns into
# a delegation for the session's thread id. A fresh one is minted per call.
# Everything is deleted on exit: both sessions and containers, the shared
# space and its directory, the users and their personal spaces.
#
#   A (session `isolation-a-<pid>`): checks 1-17, 20, 21
#   B (session `isolation-b-<pid>`): checks 18, 19, 22
#
# ---- The "no published port" problem -------------------------------------
#
# `code-exec-manager` has no published port (M4-03's intentional compose
# design — only reachable at `http://code-exec-manager:8090` from inside its
# own bridge network, never from this script's own host/localhost). As of
# M7-01, that network is `homeai-internal` (`internal: true` — no route to
# the public internet — see docs/ARCHITECTURE.md §5's "Network segmentation"
# section), not `homeai-net`; `internal: true` only removes the network's
# own default route/NAT out, it does NOT block containers on the same
# network from reaching each other. Worked around by spinning up a
# throwaway "runner" container (`python:3.12-slim`, already present on this
# host and on `homeai-internal` via `--network`) for the script's duration,
# then `docker exec`-ing a small `urllib.request`-based Python snippet into
# it for every REST call (delegation minting on `platform:8100`, then
# ensure/execute/delete on the manager) — that container has Python but no
# `curl`, and `curl` isn't installed on the HOST either. The runner is named
# `verify-isolation-runner-$$` (PID-suffixed so concurrent runs never
# collide) and is removed in the EXIT trap, so re-running this script is
# always safe.
#
# The compose network name is resolved at runtime via
# `docker compose config --format json` (not hardcoded as a guess).
#
# ---- cgroup v1 vs v2 (check 13) -------------------------------------------
#
# Determined INSIDE the exec container at check-time (`test -f
# /sys/fs/cgroup/cpu.max`), not assumed from the host — this host is cgroup
# v2, and Docker's private per-container cgroup namespace exposes that same
# v2 unified-hierarchy layout inside the exec container too (the v1 layout
# is handled as the `else` branch below for portability).
#
# **`nproc` caveat, verified independently of code-exec-manager**: `nproc`
# does NOT reflect the cgroup v2 CPU quota on this host's Docker + GNU
# coreutils 9.4 combination — confirmed with a bare
# `docker run --rm --cpus=4 ubuntu:24.04 nproc` (prints the host's full
# core count, not 4), even though `cpu.max` inside that same container
# correctly reads `400000 100000`. Check 13 therefore gates on the
# `cpu.max` quota/period ratio, not on `nproc`'s own output (still logged).
#
# ---- Check 14 (mount parsing) ---------------------------------------------
#
# `mount`'s output is parsed with a regex matching its standard
# `SOURCE on TARGET type FSTYPE (OPTIONS)` line shape, then filtered down to
# mounts that are (a) not `tmpfs` and (b) not one of the pseudo-filesystems
# every container gets for free regardless of this hardening spec (`proc`,
# `sysfs`, `cgroup`/`cgroup2`, `devpts`, `mqueue`, `overlay` — several of
# which are mounted `rw` by Docker itself). What's left are real bind
# mounts; the `rw` ones must be exactly the user's writable spaces, the `ro`
# ones under `/files` exactly their viewer spaces (`/etc/resolv.conf`,
# `/etc/hostname`, `/etc/hosts` are `ro` binds outside `/files`).
#
# ---- Needs no sudo ---------------------------------------------------------
#
# Host-side ownership (check 20) is read with `stat` inside the platform
# container: the host's space dirs are `root:<gid> 2770`, which the invoking
# user can't traverse.
#
# Usage:
#   scripts/verify_isolation.sh
#
# Any check failing prints it in RED and the suite continues (collects ALL
# failures rather than stopping at the first) then exits 1 at the end if
# anything failed.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "$REPO_ROOT"
# shellcheck source=e2e/lib/auth.sh
source "${SCRIPT_DIR}/e2e/lib/auth.sh"

SESSION_A="isolation-a-$$"
SESSION_B="isolation-b-$$"
CONTAINER_A="homeai-exec-${SESSION_A}"
CONTAINER_B="homeai-exec-${SESSION_B}"
SPACE="e2e-iso-$(openssl rand -hex 3)"
MARK="iso-mark-$(openssl rand -hex 4)"
RUNNER_NAME="verify-isolation-runner-$$"
RUNNER_IMAGE="python:3.12-slim"
RUNNER_STARTED=0

RED=$'\033[0;31m'
GREEN=$'\033[0;32m'
NC=$'\033[0m'

PASS_COUNT=0
FAIL_COUNT=0
FAILED_CHECKS=()

log() {
  echo "[verify-isolation] $(date '+%H:%M:%S') $*"
}

pass() {
  PASS_COUNT=$((PASS_COUNT + 1))
  printf '%sPASS%s [%2d] %s\n' "$GREEN" "$NC" "$1" "$2"
}

fail() {
  FAIL_COUNT=$((FAIL_COUNT + 1))
  FAILED_CHECKS+=("$1")
  printf '%sFAIL%s [%2d] %s\n' "$RED" "$NC" "$1" "$2"
  if [ -n "${3:-}" ]; then
    printf '%s       -> %s%s\n' "$RED" "$3" "$NC"
  fi
}

# ---- REST helper (urllib inside the runner container - see header) --------

# argv: method session_id action(ensure|execute|delete) auth timeout [json body]
# auth: `delegation` (for session_id's own thread), `delegation-for:<thread>`,
# `tampered` (a real delegation with a broken signature), or `none`.
# Prints the manager's JSON response, `{"status": N}` for an empty 2xx, or
# `{"http_error": N, "body": ...}`.
PY_CALL="$(cat <<'EOF'
import json, os, sys, urllib.error, urllib.request

PLATFORM = "http://platform:8100"
MANAGER = "http://code-exec-manager:8090"
method, session_id, action, auth, timeout = sys.argv[1:6]
body = sys.argv[6] if len(sys.argv) > 6 else ""
timeout = int(timeout)


def call(url, data=None, method="GET", headers=None, t=30):
    req = urllib.request.Request(url, data=data, method=method, headers=headers or {})
    return urllib.request.urlopen(req, timeout=t)


def delegation(thread):
    with call(f"{PLATFORM}/internal/auth/verify", headers={"Cookie": os.environ["ISO_COOKIE"]}) as r:
        identity = r.headers["X-HomeAI-Identity"]
    payload = json.dumps({"identity_token": identity, "thread_id": thread}).encode()
    headers = {
        "Authorization": f"Bearer {os.environ['PLATFORM_AGENT_TOKEN']}",
        "Content-Type": "application/json",
    }
    with call(f"{PLATFORM}/internal/delegations", payload, "POST", headers) as r:
        return json.loads(r.read())["token"]


headers = {"Content-Type": "application/json"}
if auth == "delegation":
    headers["Authorization"] = f"Bearer {delegation(session_id)}"
elif auth.startswith("delegation-for:"):
    headers["Authorization"] = f"Bearer {delegation(auth.split(':', 1)[1])}"
elif auth == "tampered":
    head, claims, signature = delegation(session_id).split(".")
    flipped = "A" if signature[0] != "A" else "B"
    headers["Authorization"] = f"Bearer {head}.{claims}.{flipped}{signature[1:]}"

path = f"/sessions/{session_id}" + ("" if action == "delete" else f"/{action}")
data = body.encode() if body else (b"" if method == "POST" else None)
try:
    with call(MANAGER + path, data, method, headers, timeout + 20) as r:
        out = r.read().decode()
        sys.stdout.write(out if out else json.dumps({"status": r.status}))
except urllib.error.HTTPError as e:
    sys.stdout.write(json.dumps({"http_error": e.code, "body": e.read().decode()}))
except urllib.error.URLError as e:
    sys.stdout.write(json.dumps({"url_error": str(e.reason)}))
EOF
)"

# $1: a|b (whose session cookie mints the delegation), then PY_CALL's argv.
manager_call() {
  local who="$1" cookie
  shift
  if [ "$who" = a ]; then cookie="$COOKIE_A"; else cookie="$COOKIE_B"; fi
  ISO_COOKIE="$cookie" docker exec -e ISO_COOKIE -e PLATFORM_AGENT_TOKEN \
    "$RUNNER_NAME" python3 -c "$PY_CALL" "$@" 2>/dev/null || true
}

session_of() {
  if [ "$1" = a ]; then echo "$SESSION_A"; else echo "$SESSION_B"; fi
}

manager_ensure() {
  local who="$1" out
  log "Creating exec session '$(session_of "$who")' as user ${who^^} via POST .../ensure ..."
  out="$(manager_call "$who" POST "$(session_of "$who")" ensure delegation 30)"
  if ! python3 -c 'import json,sys; sys.exit(0 if "container_id" in json.loads(sys.argv[1]) else 1)' "${out:-{\}}" 2>/dev/null; then
    log "ERROR: ensure as ${who^^} failed: ${out:-<no output - is code-exec-manager reachable on ${NETWORK_NAME}?>}"
    exit 1
  fi
  log "OK: ensure -> ${out}"
}

# $1: a|b. $2: command to run inside that user's exec container. $3: execute
# timeout_seconds. Prints the raw JSON response from the manager's `execute`
# endpoint (never raises - a failure becomes a synthetic JSON blob so every
# check's own validator can fail cleanly instead of aborting the suite).
manager_execute_as() {
  local who="$1" command="$2" timeout_seconds="${3:-15}" body out
  body="$(python3 -c 'import json,sys; print(json.dumps({"command": sys.argv[1], "timeout_seconds": int(sys.argv[2])}))' "$command" "$timeout_seconds")"
  out="$(manager_call "$who" POST "$(session_of "$who")" execute delegation "$timeout_seconds" "$body")"
  if [ -z "$out" ]; then
    out='{"stdout":"","stderr":"[verify_isolation] docker exec to runner failed or returned empty output","exit_code":-1,"timed_out":false,"duration_ms":0,"truncated":false}'
  fi
  printf '%s' "$out"
}

manager_execute() {
  manager_execute_as a "$@"
}

# ---- shared python validators ----------------------------------------------
# Each receives the manager `execute` response JSON as argv[1] (plus
# occasional extra args), exits 0 for pass / non-zero for fail, and may
# print a one-line reason - captured as the FAIL detail line.

VALIDATOR_NONZERO_EXIT="$(cat <<'EOF'
import json, sys
data = json.loads(sys.argv[1])
if data.get("exit_code", 0) == 0:
    print(f"expected nonzero exit_code, got 0 (stdout={data.get('stdout')!r})")
    sys.exit(1)
EOF
)"

VALIDATOR_ZERO_EXIT="$(cat <<'EOF'
import json, sys
data = json.loads(sys.argv[1])
if data.get("exit_code") != 0:
    print(f"expected exit_code 0, got {data.get('exit_code')} (stderr={data.get('stderr')!r}, body={data.get('body')!r})")
    sys.exit(1)
EOF
)"

VALIDATOR_NO_SECRET_ENV="$(cat <<'EOF'
import json, sys
data = json.loads(sys.argv[1])
if data.get("exit_code") != 0:
    print(f"expected exit_code 0 from `env`, got {data.get('exit_code')}")
    sys.exit(1)
FORBIDDEN = ["POSTGRES", "MODEL_", "SECRET", "PASSWORD", "API_KEY", "TOKEN", "PRIVATE_KEY"]
stdout = data.get("stdout", "")
hits = [f for f in FORBIDDEN if f.lower() in stdout.lower()]
if hits:
    print(f"env leaked secret-shaped var(s) matching {hits!r}: {stdout!r}")
    sys.exit(1)
EOF
)"

VALIDATOR_UID="$(cat <<'EOF'
import json, sys
data = json.loads(sys.argv[1])
expected = sys.argv[2]
if data.get("exit_code") != 0:
    print(f"expected exit_code 0, got {data.get('exit_code')}")
    sys.exit(1)
uid = data.get("stdout", "").strip()
if uid == "0":
    print("running as root (uid 0) - hardening spec violated")
    sys.exit(1)
if uid != expected:
    print(f"expected the user's uid {expected!r}, got {uid!r}")
    sys.exit(1)
EOF
)"

VALIDATOR_CAPEFF_ZERO="$(cat <<'EOF'
import json, sys
data = json.loads(sys.argv[1])
if data.get("exit_code") != 0:
    print(f"expected exit_code 0, got {data.get('exit_code')} stderr={data.get('stderr')!r}")
    sys.exit(1)
stdout = data.get("stdout", "").strip()
if ":" not in stdout:
    print(f"unexpected CapEff line: {stdout!r}")
    sys.exit(1)
value = stdout.split(":", 1)[1].strip()
if int(value, 16) != 0:
    print(f"CapEff is not all-zero: {value!r}")
    sys.exit(1)
EOF
)"

VALIDATOR_1="$(cat <<'EOF'
import json, sys
data = json.loads(sys.argv[1])
if data.get("exit_code") != 0:
    print(f"expected exit_code 0, got {data.get('exit_code')} stderr={data.get('stderr')!r}")
    sys.exit(1)
route_rows, ifaces = [], []
for line in data.get("stdout", "").splitlines():
    if not line.strip():
        continue
    if line.startswith("Iface"):
        continue  # /proc/net/route header - always present, never a real route
    if "\t" in line:
        route_rows.append(line)  # a real /proc/net/route data row
    else:
        ifaces.append(line.strip())  # one `ls /sys/class/net` entry
if route_rows:
    print(f"unexpected route table row(s) (implies a route exists): {route_rows!r}")
    sys.exit(1)
if ifaces != ["lo"]:
    print(f"unexpected network interface(s): {ifaces!r} (expected only 'lo')")
    sys.exit(1)
EOF
)"

VALIDATOR_13="$(cat <<'EOF'
import json, sys
data = json.loads(sys.argv[1])
if data.get("exit_code") != 0:
    print(f"expected exit_code 0, got {data.get('exit_code')} stderr={data.get('stderr')!r}")
    sys.exit(1)
lines = data.get("stdout", "").splitlines()
if not lines:
    print("empty stdout")
    sys.exit(1)
nproc_val = lines[0].strip()
try:
    cg_idx = lines.index("---CGROUP---")
    mem_idx = lines.index("---MEM---")
except ValueError:
    print(f"malformed output (missing markers): {lines!r}")
    sys.exit(1)
version = lines[cg_idx + 1].strip()
if version == "v2":
    quota_period = lines[cg_idx + 2].split()
    quota, period = quota_period[0], quota_period[1]
elif version == "v1":
    quota = lines[cg_idx + 2].strip()
    period = lines[cg_idx + 3].strip()
else:
    print(f"unrecognized cgroup version marker: {version!r}")
    sys.exit(1)
mem_max = lines[mem_idx + 1].strip()

failures = []
if quota in ("max", "-1"):
    failures.append(f"cpu quota is unlimited ({quota!r}) - no CPU limit enforced")
else:
    cores = int(quota) / int(period)
    if cores > 4:
        failures.append(f"cpu quota implies {cores} cores (> 4): quota={quota} period={period}")
if mem_max != "4294967296":
    failures.append(f"memory.max = {mem_max!r}, expected 4294967296 (4 GiB)")

note = f"(info: nproc={nproc_val} [not cgroup-aware here, ignored]; cgroup {version} cpu.max={quota}/{period}; memory.max={mem_max})"
if failures:
    print("; ".join(failures) + " " + note)
    sys.exit(1)
print(note)
EOF
)"

# argv[2]: comma-separated targets that must be the ONLY rw binds.
# argv[3]: comma-separated targets that must be the ONLY ro binds under /files.
VALIDATOR_MOUNTS="$(cat <<'EOF'
import json, re, sys

data = json.loads(sys.argv[1])
want_rw = set(filter(None, sys.argv[2].split(",")))
want_ro = set(filter(None, sys.argv[3].split(",")))
if data.get("exit_code") != 0:
    print(f"expected exit_code 0, got {data.get('exit_code')} stderr={data.get('stderr')!r}")
    sys.exit(1)

PSEUDO_FS = {"proc", "sysfs", "cgroup", "cgroup2", "devpts", "mqueue", "overlay"}
PATTERN = re.compile(r"^(?P<source>.*) on (?P<target>.*) type (?P<fstype>\S+) \((?P<opts>[^)]*)\)$")

rw, ro = set(), set()
for line in data.get("stdout", "").splitlines():
    m = PATTERN.match(line.strip())
    if not m or m.group("fstype") == "tmpfs" or m.group("fstype") in PSEUDO_FS:
        continue
    opts = m.group("opts").split(",")
    target = m.group("target")
    if "rw" in opts:
        rw.add(target)
    elif target == "/files" or target.startswith("/files/"):
        ro.add(target)

errors = []
if rw != want_rw:
    errors.append(f"rw binds {sorted(rw)} != expected {sorted(want_rw)}")
if ro != want_ro:
    errors.append(f"ro binds under /files {sorted(ro)} != expected {sorted(want_ro)}")
if errors:
    print("; ".join(errors))
    sys.exit(1)
EOF
)"

# argv[2]: JSON {"user": "uid:gid", "group_add": [...], "binds": {source: [target, rw]}}.
VALIDATOR_INSPECT="$(cat <<'EOF'
import json, sys
data = json.loads(sys.argv[1])[0]
want = json.loads(sys.argv[2])
hc = data.get("HostConfig", {})
errors = []
if hc.get("NetworkMode") != "none":
    errors.append(f"NetworkMode={hc.get('NetworkMode')!r}, expected 'none'")
if hc.get("ReadonlyRootfs") is not True:
    errors.append(f"ReadonlyRootfs={hc.get('ReadonlyRootfs')!r}, expected True")
if hc.get("CapDrop") != ["ALL"]:
    errors.append(f"CapDrop={hc.get('CapDrop')!r}, expected ['ALL']")
if hc.get("Privileged") is not False:
    errors.append(f"Privileged={hc.get('Privileged')!r}, expected False")
if data.get("Config", {}).get("User") != want["user"]:
    errors.append(f"User={data.get('Config', {}).get('User')!r}, expected {want['user']!r}")
if sorted(hc.get("GroupAdd") or []) != sorted(want["group_add"]):
    errors.append(f"GroupAdd={hc.get('GroupAdd')!r}, expected {want['group_add']!r}")
binds = {m["Source"]: [m["Destination"], m["RW"]] for m in data.get("Mounts", []) if m.get("Type") == "bind"}
if binds != want["binds"]:
    errors.append(f"bind mounts {binds!r} != expected {want['binds']!r}")
if errors:
    print("; ".join(errors))
    sys.exit(1)
EOF
)"

# ---- setup / teardown -------------------------------------------------------

cli() { _e2e_compose exec -T platform python -m app.cli "$@" >/dev/null; }

preflight() {
  log "Resolving compose network name for 'homeai-internal' (M7-01 - where code-exec-manager lives) ..."
  NETWORK_NAME="$(docker compose config --format json | python3 -c "
import json, sys
print(json.load(sys.stdin)['networks']['homeai-internal']['name'])
")"
  if ! docker network ls --format '{{.Name}}' | grep -qx "$NETWORK_NAME"; then
    log "ERROR: resolved network '${NETWORK_NAME}' not found via 'docker network ls' - is the stack up?"
    exit 1
  fi
  log "OK: using compose network '${NETWORK_NAME}'"

  SPACES_DIR="$(_e2e_env_value SPACES_DIR "")"
  PLATFORM_AGENT_TOKEN="$(_e2e_env_value PLATFORM_AGENT_TOKEN "")"
  export PLATFORM_AGENT_TOKEN
  if [ -z "$SPACES_DIR" ] || [ -z "$PLATFORM_AGENT_TOKEN" ]; then
    log "ERROR: SPACES_DIR/PLATFORM_AGENT_TOKEN not set in .env"
    exit 1
  fi
  log "OK: SPACES_DIR=${SPACES_DIR}; PLATFORM_AGENT_TOKEN is set"

  HOST_LAN_IP="$(ip route get 1.1.1.1 2>/dev/null | sed -n 's/.* src \([0-9.]*\).*/\1/p' | head -n1)"
  if [ -z "$HOST_LAN_IP" ]; then
    HOST_LAN_IP="$(hostname -I | awk '{print $1}')"
  fi
  log "OK: host LAN IP (for check 3) = ${HOST_LAN_IP:-<unresolved>}"
}

# Prints "<personal space id> <personal gid> <uid>" for a username.
personal_of() {
  _e2e_psql homeai_platform "SELECT s.id || ' ' || s.gid || ' ' || u.uid FROM spaces s
    JOIN users u ON u.id = s.owner_user_id WHERE s.kind = 'personal' AND u.username = '$1'"
}

setup_users() {
  log "Creating users A and B and shared space ${SPACE} (A owner, B viewer) ..."
  e2e_auth_create_user iso-a
  USER_A="$E2E_NEW_USER"
  COOKIE_A="$(e2e_auth_login "$USER_A" "$E2E_NEW_PASSWORD")"
  e2e_auth_create_user iso-b
  USER_B="$E2E_NEW_USER"
  COOKIE_B="$(e2e_auth_login "$USER_B" "$E2E_NEW_PASSWORD")"
  cli create-space "$SPACE" --name "E2E isolation" --owner "$USER_A"
  cli add-member "$SPACE" "$USER_B" --role viewer

  read -r HOME_A GID_A UID_A <<<"$(personal_of "$USER_A")"
  read -r HOME_B GID_B UID_B <<<"$(personal_of "$USER_B")"
  read -r SPACE_ID GID_S <<<"$(_e2e_psql homeai_platform \
    "SELECT id || ' ' || gid FROM spaces WHERE slug = '${SPACE}'")"
  local id
  for id in "$HOME_A" "$HOME_B" "$SPACE_ID"; do
    if ! [[ "$id" =~ ^[0-9a-f-]{36}$ ]]; then
      log "ERROR: couldn't resolve the test users' spaces (got '${id}')"
      exit 1
    fi
  done
  SHARED="/files/spaces/${SPACE}"
  log "OK: A=${USER_A} (uid ${UID_A}, personal gid ${GID_A}); B=${USER_B} (uid ${UID_B}," \
    "personal gid ${GID_B}); ${SPACE} gid ${GID_S}"
}

start_runner() {
  log "Starting runner container (${RUNNER_NAME}) on ${NETWORK_NAME} to drive the platform + manager REST APIs..."
  docker run -d --rm --name "$RUNNER_NAME" --network "$NETWORK_NAME" "$RUNNER_IMAGE" sleep infinity >/dev/null
  RUNNER_STARTED=1
  local tries=0
  while ! docker exec "$RUNNER_NAME" true >/dev/null 2>&1; do
    tries=$((tries + 1))
    if [ "$tries" -ge 30 ]; then
      log "ERROR: runner container never became exec-able"
      exit 1
    fi
    sleep 0.5
  done
  log "OK: runner is exec-able"
}

cleanup() {
  if [ "$RUNNER_STARTED" = "1" ]; then
    [ -n "${COOKIE_A:-}" ] && manager_call a DELETE "$SESSION_A" delete delegation 10 >/dev/null
    [ -n "${COOKIE_B:-}" ] && manager_call b DELETE "$SESSION_B" delete delegation 10 >/dev/null
    docker rm -f "$RUNNER_NAME" >/dev/null 2>&1 || true
  fi
  # Defensive: in case a DELETE never landed (or check 22 left B in A's session).
  docker rm -f "$CONTAINER_A" "$CONTAINER_B" >/dev/null 2>&1 || true
  if [ -n "${SPACE_ID:-}" ] && [[ "$SPACE_ID" =~ ^[0-9a-f-]{36}$ ]]; then
    _e2e_psql homeai_platform "DELETE FROM spaces WHERE id = '${SPACE_ID}'" >/dev/null 2>&1 || true
    _e2e_compose exec -T platform rm -rf "/data/spaces/${SPACE_ID}" >/dev/null 2>&1 || true
  fi
  e2e_auth_end
}
trap cleanup EXIT

# ---- checks 1-14 (A, through the manager's `execute` endpoint) ------------

check_generic() {
  local num="$1" desc="$2" command="$3" validator="$4" timeout="${5:-15}"
  local json out rc
  json="$(manager_execute "$command" "$timeout")"
  # NOTE: `out=$(...)` is deliberately the CONDITION of this `if` (never a
  # bare top-level assignment) - under `set -e`, a bare
  # `out="$(cmd)"; rc=$?` sequence exits the whole script the instant `cmd`
  # returns non-zero, before `rc=$?` is ever reached. Wrapping the
  # assignment itself in `if`/`else` is the one form `set -e` exempts, and
  # is required in every check below for exactly this reason.
  if out="$(python3 -c "$validator" "$json" 2>&1)"; then
    rc=0
  else
    rc=$?
  fi
  if [ "$rc" -eq 0 ]; then
    pass "$num" "$desc"
  else
    fail "$num" "$desc" "$out"
  fi
}

check_1() {
  check_generic 1 "no interfaces besides 'lo', no route table entries" \
    'cat /proc/net/route; ls /sys/class/net' "$VALIDATOR_1" 10
}

check_2() {
  check_generic 2 "no network reachability to agent-server (DNS/connect failure)" \
    'curl -m 3 http://agent-server:8000/api/health' "$VALIDATOR_NONZERO_EXIT" 10
}

check_3() {
  local json1 json2 out1 out2 rc1 rc2
  json1="$(manager_execute 'curl -m 3 http://192.168.1.1' 10)"
  if out1="$(python3 -c "$VALIDATOR_NONZERO_EXIT" "$json1" 2>&1)"; then
    rc1=0
  else
    rc1=$?
  fi
  json2="$(manager_execute "curl -m 3 http://${HOST_LAN_IP}" 10)"
  if out2="$(python3 -c "$VALIDATOR_NONZERO_EXIT" "$json2" 2>&1)"; then
    rc2=0
  else
    rc2=$?
  fi
  if [ "$rc1" -eq 0 ] && [ "$rc2" -eq 0 ]; then
    pass 3 "no connect to 192.168.1.1 or host LAN IP (${HOST_LAN_IP})"
  else
    local detail=""
    if [ "$rc1" -ne 0 ]; then
      detail="192.168.1.1: ${out1}"
    fi
    if [ "$rc2" -ne 0 ]; then
      if [ -n "$detail" ]; then
        detail="${detail}; "
      fi
      detail="${detail}${HOST_LAN_IP}: ${out2}"
    fi
    fail 3 "no connect to 192.168.1.1 or host LAN IP (${HOST_LAN_IP})" "$detail"
  fi
}

check_4() {
  check_generic 4 "docker.sock not present inside the exec container" \
    'ls /var/run/docker.sock /run/docker.sock' "$VALIDATOR_NONZERO_EXIT" 10
}

check_5() {
  check_generic 5 "no /app or /data paths leaked into the exec container" \
    'ls /app /data' "$VALIDATOR_NONZERO_EXIT" 10
}

check_6() {
  check_generic 6 "no secret-shaped env vars leaked into the exec container" \
    'env' "$VALIDATOR_NO_SECRET_ENV" 10
}

check_7() {
  check_generic 7 "root filesystem (and /files itself) is read-only" \
    'touch /forbidden || touch /files/forbidden || touch /files/spaces/forbidden' \
    "$VALIDATOR_NONZERO_EXIT" 10
}

check_8() {
  check_generic 8 '/tmp and $HOME are writable (tmpfs)' \
    'touch /tmp/x && touch $HOME/x' "$VALIDATOR_ZERO_EXIT" 10
}

check_9() {
  check_generic 9 "/files/personal and the owned space are writable (rw binds)" \
    "touch /files/personal/isolation-ok && rm /files/personal/isolation-ok && touch ${SHARED}/isolation-ok && rm ${SHARED}/isolation-ok" \
    "$VALIDATOR_ZERO_EXIT" 10
}

check_10() {
  local json out rc
  json="$(manager_execute 'id -u' 10)"
  if out="$(python3 -c "$VALIDATOR_UID" "$json" "$UID_A" 2>&1)"; then
    rc=0
  else
    rc=$?
  fi
  if [ "$rc" -eq 0 ]; then
    pass 10 "runs as the user's own uid (${UID_A}), not root"
  else
    fail 10 "runs as the user's own uid (${UID_A}), not root" "$out"
  fi
}

check_11() {
  check_generic 11 "all Linux capabilities dropped (CapEff all-zero)" \
    'grep CapEff /proc/self/status' "$VALIDATOR_CAPEFF_ZERO" 10
}

check_12() {
  check_generic 12 "no raw-socket network reachability (python socket connect fails)" \
    'python3 -c "import socket; socket.create_connection((\"1.1.1.1\",80),3)"' "$VALIDATOR_NONZERO_EXIT" 10
}

check_13() {
  local json out rc
  json="$(manager_execute 'nproc; echo ---CGROUP---; if [ -f /sys/fs/cgroup/cpu.max ]; then echo v2; cat /sys/fs/cgroup/cpu.max; echo ---MEM---; cat /sys/fs/cgroup/memory.max; else echo v1; cat /sys/fs/cgroup/cpu/cpu.cfs_quota_us; cat /sys/fs/cgroup/cpu/cpu.cfs_period_us; echo ---MEM---; cat /sys/fs/cgroup/memory/memory.limit_in_bytes; fi' 10)"
  if out="$(python3 -c "$VALIDATOR_13" "$json" 2>&1)"; then
    rc=0
  else
    rc=$?
  fi
  if [ -n "$out" ]; then
    log "  (check 13) ${out}"
  fi
  if [ "$rc" -eq 0 ]; then
    pass 13 "CPU quota <=4 cores (cgroup cpu.max) and memory.max == 4 GiB"
  else
    fail 13 "CPU quota <=4 cores (cgroup cpu.max) and memory.max == 4 GiB"
  fi
}

check_14() {
  local json out rc
  json="$(manager_execute 'mount' 10)"
  if out="$(python3 -c "$VALIDATOR_MOUNTS" "$json" "/files/personal,${SHARED}" "" 2>&1)"; then
    rc=0
  else
    rc=$?
  fi
  if [ "$rc" -eq 0 ]; then
    pass 14 "the only rw non-tmpfs mounts are /files/personal and ${SHARED}"
  else
    fail 14 "the only rw non-tmpfs mounts are /files/personal and ${SHARED}" "$out"
  fi
}

# ---- checks 15-17 (stack-level, directly on the host via docker) ----------

check_15() {
  local log_file
  log_file="$(mktemp)"
  if bash "${REPO_ROOT}/scripts/check_socket_exclusivity.sh" >"$log_file" 2>&1; then
    pass 15 "check_socket_exclusivity.sh exits 0 (only code-exec-manager holds docker.sock)"
  else
    fail 15 "check_socket_exclusivity.sh exits 0 (only code-exec-manager holds docker.sock)" "$(cat "$log_file")"
  fi
  rm -f "$log_file"
}

# $1: check number, $2: container, $3: expected JSON for VALIDATOR_INSPECT, $4: description.
check_inspect() {
  local num="$1" container="$2" want="$3" desc="$4" inspect_json out rc
  if ! inspect_json="$(docker inspect "$container" 2>&1)"; then
    fail "$num" "$desc" "docker inspect failed: ${inspect_json}"
    return
  fi
  if out="$(python3 -c "$VALIDATOR_INSPECT" "$inspect_json" "$want" 2>&1)"; then
    rc=0
  else
    rc=$?
  fi
  if [ "$rc" -eq 0 ]; then
    pass "$num" "$desc"
  else
    fail "$num" "$desc" "$out"
  fi
}

check_16() {
  local want
  want="$(printf '{"user": "%s:%s", "group_add": ["%s"], "binds": {"%s": ["/files/personal", true], "%s": ["%s", true]}}' \
    "$UID_A" "$GID_A" "$GID_S" "${SPACES_DIR}/${HOME_A}/files" "${SPACES_DIR}/${SPACE_ID}/files" "$SHARED")"
  check_inspect 16 "$CONTAINER_A" "$want" \
    "docker inspect: NetworkMode/ReadonlyRootfs/CapDrop/Privileged, User=uid:personal gid, GroupAdd, one bind per space"
}

check_17() {
  local offenders
  offenders="$(docker compose config --format json | python3 -c "
import json, sys
cfg = json.load(sys.stdin)
bad = [name for name, svc in cfg.get('services', {}).items() if name != 'caddy' and svc.get('ports')]
print('\n'.join(bad))
")"
  if [ -z "$offenders" ]; then
    pass 17 "only 'caddy' publishes host ports (docker compose config)"
  else
    fail 17 "only 'caddy' publishes host ports (docker compose config)" "service(s) with published ports: ${offenders}"
  fi
}

# ---- checks 18-22 (per-user exec, M11-03) ----------------------------------

# $1 check number, $2 description, $3 python assertion over `r` (the execute
# response dict), $4 who, $5 command. Prints nothing on pass.
check_exec_py() {
  local num="$1" desc="$2" assertion="$3" who="$4" command="$5" json out rc
  json="$(manager_execute_as "$who" "$command" 15)"
  if out="$(python3 -c "
import json, sys
r = json.loads(sys.argv[1])
$assertion
" "$json" 2>&1)"; then
    rc=0
  else
    rc=$?
  fi
  if [ "$rc" -eq 0 ]; then
    pass "$num" "$desc"
  else
    fail "$num" "$desc" "${out} (response: ${json})"
  fi
}

check_18() {
  manager_execute_as a "echo ${MARK} > /files/personal/${MARK}.txt" 10 >/dev/null
  check_exec_py 18 "B's container can't see A's personal space (only B's own /files/personal and ${SHARED})" "
out = r.get('stdout', '')
assert r.get('exit_code') == 0, f'exit_code {r.get(\"exit_code\")}: {r.get(\"stderr\")!r}'
spaces, personal, hits = out.split('---')
assert spaces.split() == ['${SPACE}'], f'/files/spaces lists {spaces.split()}'
assert '${MARK}' not in personal, f'A\\'s marker in B\\'s /files/personal: {personal!r}'
assert not hits.strip(), f'A\\'s marker found under B\\'s /files: {hits!r}'
" b "ls /files/spaces; echo ---; ls -a /files/personal; echo ---; grep -rl ${MARK} /files 2>/dev/null; true"
}

check_19() {
  manager_execute_as a "echo shared-${MARK} > ${SHARED}/iso-shared.txt" 10 >/dev/null
  check_exec_py 19 "viewer mount is read-only: B reads ${SHARED} but can't write, though in its group (${GID_S})" "
out = r.get('stdout', '')
lines = out.splitlines()
assert lines[0] == 'shared-${MARK}', f'B read {lines[:1]!r}'
assert '${GID_S}' in lines[1].split(), f'B not in the space group: id -G = {lines[1]!r}'
assert lines[2] != '0', 'B wrote into the viewer space'
assert 'Read-only file system' in r.get('stderr', ''), f'stderr {r.get(\"stderr\")!r}'
" b "cat ${SHARED}/iso-shared.txt; id -G; touch ${SHARED}/viewer-write; echo \$?"
  local json out rc
  json="$(manager_execute_as b 'mount' 10)"
  if out="$(python3 -c "$VALIDATOR_MOUNTS" "$json" "/files/personal" "$SHARED" 2>&1)"; then
    rc=0
  else
    rc=$?
  fi
  if [ "$rc" -eq 0 ]; then
    pass 19 "B's mounts: /files/personal rw, ${SHARED} ro, nothing else"
  else
    fail 19 "B's mounts: /files/personal rw, ${SHARED} ro, nothing else" "$out"
  fi
  local want
  want="$(printf '{"user": "%s:%s", "group_add": ["%s"], "binds": {"%s": ["/files/personal", true], "%s": ["%s", false]}}' \
    "$UID_B" "$GID_B" "$GID_S" "${SPACES_DIR}/${HOME_B}/files" "${SPACES_DIR}/${SPACE_ID}/files" "$SHARED")"
  check_inspect 19 "$CONTAINER_B" "$want" "docker inspect B: its own uid/gids, A's personal space not bound, ${SHARED} bound ro"
}

check_20() {
  check_exec_py 20 "files A creates are <uid>:<space gid> and group-writable (dirs setgid)" "
got = r.get('stdout', '').split()
want = ['${UID_A}:${GID_S}:2775', '${UID_A}:${GID_S}:664', '${UID_A}:${GID_A}:664']
assert got == want, f'stat gave {got}, expected {want} (stderr {r.get(\"stderr\")!r})'
" a "mkdir -p ${SHARED}/iso-dir && echo x > ${SHARED}/iso-dir/f && stat -c '%u:%g:%a' ${SHARED}/iso-dir ${SHARED}/iso-dir/f /files/personal/${MARK}.txt"
  local got expected
  got="$(_e2e_compose exec -T platform stat -c '%u:%g:%a' \
    "/data/spaces/${SPACE_ID}/files/iso-dir/f" "/data/spaces/${HOME_A}/files/${MARK}.txt" 2>&1 | tr '\n' ' ')"
  expected="${UID_A}:${GID_S}:664 ${UID_A}:${GID_A}:664 "
  if [ "$got" = "$expected" ]; then
    pass 20 "on the host too: \${SPACES_DIR}/<space>/files/... is ${UID_A}:${GID_S} 664, personal ${UID_A}:${GID_A} 664"
  else
    fail 20 "on the host too: files are <uid>:<space gid> 664" "stat gave '${got}', expected '${expected}'"
  fi
}

check_21() {
  local desc="session calls without a valid delegation are refused (401), another thread's is 403"
  local results
  results="$(printf '%s\n' \
    "$(manager_call a POST "$SESSION_A" ensure none 10)" \
    "$(manager_call a POST "$SESSION_A" ensure tampered 10)" \
    "$(manager_call a POST "$SESSION_A" execute none 10 '{"command": "id"}')" \
    "$(manager_call a DELETE "$SESSION_A" delete none 10)" \
    "$(manager_call a POST "$SESSION_A" ensure "delegation-for:${SESSION_B}" 10)" \
    "$(manager_call a POST "$SESSION_A" execute "delegation-for:${SESSION_B}" 10 '{"command": "id"}')")"
  local out rc
  if out="$(python3 -c '
import json, sys
codes = [json.loads(line).get("http_error") for line in sys.argv[1].splitlines()]
want = [401, 401, 401, 401, 403, 403]
if codes != want:
    print(f"got {codes}, expected {want}")
    sys.exit(1)
' "$results" 2>&1)" && docker inspect "$CONTAINER_A" >/dev/null 2>&1; then
    rc=0
  else
    rc=1
    if [ -z "$out" ]; then
      out="A's container is gone after the refused calls"
    fi
  fi
  if [ "$rc" -eq 0 ]; then
    pass 21 "$desc"
  else
    fail 21 "$desc" "$out"
  fi
}

check_22() {
  local desc="B can't take over A's session: DELETE is 403; ensure replaces it with B's own grants"
  local delete ensure out rc
  delete="$(manager_call b DELETE "$SESSION_A" delete "delegation-for:${SESSION_A}" 10)"
  if ! docker inspect "$CONTAINER_A" >/dev/null 2>&1; then
    fail 22 "$desc" "A's container was removed by B's DELETE (${delete})"
    return
  fi
  ensure="$(manager_call b POST "$SESSION_A" ensure "delegation-for:${SESSION_A}" 30)"
  local want
  want="$(printf '{"user": "%s:%s", "group_add": ["%s"], "binds": {"%s": ["/files/personal", true], "%s": ["%s", false]}}' \
    "$UID_B" "$GID_B" "$GID_S" "${SPACES_DIR}/${HOME_B}/files" "${SPACES_DIR}/${SPACE_ID}/files" "$SHARED")"
  if out="$(python3 -c '
import json, sys
delete, ensure = json.loads(sys.argv[1]), json.loads(sys.argv[2])
if delete.get("http_error") != 403:
    print(f"B DELETE of A session: {delete}")
    sys.exit(1)
if ensure.get("created") is not True:
    print(f"B ensure of A session did not recreate: {ensure}")
    sys.exit(1)
' "$delete" "$ensure" 2>&1)" \
    && out="$(python3 -c "$VALIDATOR_INSPECT" "$(docker inspect "$CONTAINER_A")" "$want" 2>&1)"; then
    rc=0
  else
    rc=1
  fi
  if [ "$rc" -eq 0 ]; then
    pass 22 "$desc"
  else
    fail 22 "$desc" "$out"
  fi
}

summary() {
  local total=$((PASS_COUNT + FAIL_COUNT))
  echo
  if [ "$FAIL_COUNT" -eq 0 ]; then
    printf '%sALL %d/%d CHECKS PASSED%s\n' "$GREEN" "$PASS_COUNT" "$total" "$NC"
  else
    printf '%s%d/%d CHECKS PASSED - %d FAILED (checks: %s)%s\n' \
      "$RED" "$PASS_COUNT" "$total" "$FAIL_COUNT" "${FAILED_CHECKS[*]}" "$NC"
  fi
}

main() {
  log "=== M4-05 + M11-03: isolation verification suite ==="
  preflight
  setup_users
  start_runner
  manager_ensure a

  check_1
  check_2
  check_3
  check_4
  check_5
  check_6
  check_7
  check_8
  check_9
  check_10
  check_11
  check_12
  check_13
  check_14
  check_15
  check_16
  check_17

  manager_ensure b
  check_18
  check_19
  check_20
  check_21
  check_22

  summary
}

main
[ "$FAIL_COUNT" -eq 0 ]

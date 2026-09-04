#!/usr/bin/env bash
# verify_isolation.sh — M4-05: scripted isolation verification suite.
#
# The product's core safety promise ("safe to let it run code") verified as
# a repeatable script instead of a one-time manual check. 14 checks drive
# commands INSIDE a live exec container THROUGH the manager's own
# `POST /sessions/{id}/execute` endpoint — never via `docker exec` straight
# into the exec container, which would bypass exactly what's being tested
# (an agent can only ever reach the container through that same endpoint,
# so that's the only path this suite is willing to trust). 3 more checks
# (15-17) inspect the compose stack itself directly via `docker`/`jq`, since
# there's no "manager endpoint" equivalent for socket-exclusivity, the exec
# container's own `docker inspect`, or the compose port-publishing policy.
#
# Verifies `services/code-exec-manager/app/sessions.py`'s `build_run_kwargs`
# §7 hardening spec exactly: `network_mode="none"`, `cap_drop=["ALL"]`,
# `security_opt=["no-new-privileges"]`, `read_only=True`, tmpfs `/tmp` +
# `/home/homeai`, `mem_limit="4g"`, `nano_cpus=4_000_000_000`,
# `user="1000:1000"`, `pids_limit=512`, single rw bind mount
# `WORKSPACE_DIR -> /workspace`.
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
# network from reaching each other, so this runner-container workaround
# still works unchanged, just joining the other network now. Worked around
# by spinning up a throwaway "runner" container (`python:3.12-slim`,
# already present on this host and on `homeai-internal` via
# `--network`) for the script's duration, then `docker exec`-ing a small
# `urllib.request`-based Python snippet into it for every REST call
# (ensure/execute/delete) — that container has Python but no `curl`, and
# `curl` isn't installed on the HOST either (same finding as every other
# `scripts/e2e/*.sh` script in this repo), so this is the same
# curl-unavailable workaround applied one layer further in. The runner is
# named `verify-isolation-runner-$$` (PID-suffixed so concurrent runs never
# collide) and is removed in the EXIT trap alongside the session itself, so
# re-running this script is always safe.
#
# The compose network name is resolved at runtime via
# `docker compose config --format json` (not hardcoded as a guess) — it
# happens to be `homeai_homeai-internal` (compose project name `homeai` +
# the network's own compose-file name `homeai-internal`), confirmed once
# against `docker network ls` during development, but resolving it live
# means this script keeps working even if the project name ever changes.
#
# ---- cgroup v1 vs v2 (check 13) -------------------------------------------
#
# Determined INSIDE the exec container at check-time (`test -f
# /sys/fs/cgroup/cpu.max`), not assumed from the host — this host is cgroup
# v2 (confirmed: `/sys/fs/cgroup/cgroup.controllers` exists), and Docker's
# private per-container cgroup namespace (`CgroupnsMode: private`, the
# default) exposes that same v2 unified-hierarchy layout inside the exec
# container too, so `/sys/fs/cgroup/cpu.max` / `memory.max` are readable
# directly at the container's cgroup root with no `/cpu/`, `/memory/`
# subdirectories (that's the v1 layout, handled as the `else` branch below
# for portability, though it doesn't fire on this host).
#
# **`nproc` caveat, verified independently of code-exec-manager**: `nproc`
# does NOT reflect the cgroup v2 CPU quota on this host's Docker + GNU
# coreutils 9.4 combination — confirmed with a bare
# `docker run --rm --cpus=4 ubuntu:24.04 nproc` (prints the host's full
# core count, not 4), even though `cpu.max` inside that same container
# correctly reads `400000 100000` (= 4 cores' worth of quota). This is a
# `nproc`/coreutils limitation on this cgroup v2 setup, not a
# code-exec-manager bug: `nano_cpus=4_000_000_000` IS applied correctly
# (verified via `cpu.max` directly, the authoritative source). Check 13
# therefore gates on the `cpu.max` quota/period ratio, not on `nproc`'s own
# (here, uninformative) output — `nproc`'s value is still captured and
# logged for visibility, just not used to pass/fail the check.
#
# ---- Check 14 (mount parsing) ---------------------------------------------
#
# `mount`'s output is parsed with a regex matching its standard
# `SOURCE on TARGET type FSTYPE (OPTIONS)` line shape, then filtered down to
# mounts that are (a) not `tmpfs` and (b) not one of the pseudo-filesystems
# every container gets for free regardless of this hardening spec (`proc`,
# `sysfs`, `cgroup`/`cgroup2`, `devpts`, `mqueue`, `overlay` — several of
# which are mounted `rw` by Docker itself, e.g. `proc on /proc type proc
# (rw,...)`, and would otherwise produce false positives against the
# ticket's "exactly one" expectation). What's left after that filter is
# real block/bind mounts; among those, exactly one should have the `rw`
# option, and its target should be `/workspace` (everything else — `/tmp`,
# `/home/homeai` — is `tmpfs`, already excluded; `/etc/resolv.conf`,
# `/etc/hostname`, `/etc/hosts` are real `ro` bind mounts from the same host
# block device, also excluded by the `rw` filter).
#
# Usage:
#   scripts/verify_isolation.sh
#
# Any check failing prints it in RED and the suite continues (collects ALL
# failures rather than stopping at the first, so a single run's output is
# enough to see the full blast radius of a regression) then exits 1 at the
# end if anything failed. Safe to re-run: the session + exec container +
# runner container are all cleaned up in an EXIT trap.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "$REPO_ROOT"

SESSION_ID="isolation-suite"
EXEC_CONTAINER_NAME="homeai-exec-${SESSION_ID}"
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

# ---- REST helpers (urllib inside the runner container - see header) -------

PY_ENSURE="$(cat <<'EOF'
import json, sys, urllib.error, urllib.request

session_id = sys.argv[1]
req = urllib.request.Request(
    f"http://code-exec-manager:8090/sessions/{session_id}/ensure",
    data=b"",
    method="POST",
)
try:
    with urllib.request.urlopen(req, timeout=30) as resp:
        sys.stdout.write(resp.read().decode())
except urllib.error.HTTPError as e:
    sys.stdout.write(json.dumps({"http_error": e.code, "body": e.read().decode()}))
except urllib.error.URLError as e:
    sys.stdout.write(json.dumps({"url_error": str(e.reason)}))
EOF
)"

PY_EXECUTE="$(cat <<'EOF'
import json, sys, urllib.error, urllib.request

session_id, command, timeout_seconds = sys.argv[1], sys.argv[2], int(sys.argv[3])
body = json.dumps({"command": command, "timeout_seconds": timeout_seconds}).encode()
req = urllib.request.Request(
    f"http://code-exec-manager:8090/sessions/{session_id}/execute",
    data=body,
    method="POST",
    headers={"Content-Type": "application/json"},
)
try:
    with urllib.request.urlopen(req, timeout=timeout_seconds + 20) as resp:
        sys.stdout.write(resp.read().decode())
except urllib.error.HTTPError as e:
    sys.stdout.write(json.dumps({"http_error": e.code, "body": e.read().decode()}))
except urllib.error.URLError as e:
    sys.stdout.write(json.dumps({"url_error": str(e.reason)}))
EOF
)"

PY_DELETE="$(cat <<'EOF'
import sys, urllib.error, urllib.request

session_id = sys.argv[1]
req = urllib.request.Request(
    f"http://code-exec-manager:8090/sessions/{session_id}",
    method="DELETE",
)
try:
    urllib.request.urlopen(req, timeout=30)
except Exception:
    pass
EOF
)"

manager_ensure() {
  log "Creating exec session '${SESSION_ID}' via POST /sessions/${SESSION_ID}/ensure ..."
  local out
  out="$(docker exec "$RUNNER_NAME" python3 -c "$PY_ENSURE" "$SESSION_ID" 2>&1)" || true
  if [ -z "$out" ]; then
    log "ERROR: ensure produced no output - is code-exec-manager reachable on ${NETWORK_NAME}?"
    exit 1
  fi
  log "OK: ensure -> ${out}"
}

# $1: command to run inside the exec container. $2: execute timeout_seconds
# (manager-side; also bounds the outer HTTP call). Prints the raw JSON
# response from the manager's `execute` endpoint (never raises - a
# docker-exec-into-the-runner failure becomes a synthetic JSON blob so
# every check's own validator can fail cleanly instead of aborting the
# whole suite).
manager_execute() {
  local command="$1" timeout_seconds="${2:-15}"
  local out
  out="$(docker exec "$RUNNER_NAME" python3 -c "$PY_EXECUTE" "$SESSION_ID" "$command" "$timeout_seconds" 2>/dev/null)" || true
  if [ -z "$out" ]; then
    out='{"stdout":"","stderr":"[verify_isolation] docker exec to runner failed or returned empty output","exit_code":-1,"timed_out":false,"duration_ms":0,"truncated":false}'
  fi
  printf '%s' "$out"
}

manager_delete() {
  docker exec "$RUNNER_NAME" python3 -c "$PY_DELETE" "$SESSION_ID" >/dev/null 2>&1 || true
}

# ---- shared check-1..14 python validators ----------------------------------
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
    print(f"expected exit_code 0, got {data.get('exit_code')} (stderr={data.get('stderr')!r})")
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
    print(f"expected uid {expected!r} (HOMEAI_UID), got {uid!r}")
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

# `nproc` itself is NOT cgroup-v2-quota-aware on this host's Docker/coreutils
# combo (verified independently, see this script's header comment) - logged
# for visibility only, never gates pass/fail.
note = f"(info: nproc={nproc_val} [not cgroup-aware here, ignored]; cgroup {version} cpu.max={quota}/{period}; memory.max={mem_max})"
if failures:
    print("; ".join(failures) + " " + note)
    sys.exit(1)
print(note)
EOF
)"

VALIDATOR_14="$(cat <<'EOF'
import json, re, sys

data = json.loads(sys.argv[1])
if data.get("exit_code") != 0:
    print(f"expected exit_code 0, got {data.get('exit_code')} stderr={data.get('stderr')!r}")
    sys.exit(1)

# Container-inherent pseudo-filesystems every container gets regardless of
# this hardening spec - several are mounted `rw` by Docker itself (e.g.
# `proc`), so they're excluded before checking "exactly one rw mount".
PSEUDO_FS = {"proc", "sysfs", "cgroup", "cgroup2", "devpts", "mqueue", "overlay"}
PATTERN = re.compile(r"^(?P<source>.*) on (?P<target>.*) type (?P<fstype>\S+) \((?P<opts>[^)]*)\)$")

candidates = []
for line in data.get("stdout", "").splitlines():
    line = line.strip()
    if not line:
        continue
    m = PATTERN.match(line)
    if not m:
        continue
    fstype = m.group("fstype")
    if fstype == "tmpfs" or fstype in PSEUDO_FS:
        continue
    opts = m.group("opts").split(",")
    if "rw" not in opts:
        continue
    candidates.append((m.group("target"), fstype, m.group("opts")))

if len(candidates) != 1:
    print(f"expected exactly 1 non-tmpfs/pseudo rw mount, found {len(candidates)}: {candidates!r}")
    sys.exit(1)
target, fstype, opts = candidates[0]
if target != "/workspace":
    print(f"the sole writable non-tmpfs mount is {target!r} (type {fstype}), expected '/workspace'")
    sys.exit(1)
EOF
)"

VALIDATOR_16="$(cat <<'EOF'
import json, sys
data = json.loads(sys.argv[1])[0]
workspace_dir = sys.argv[2]
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
binds = [m for m in data.get("Mounts", []) if m.get("Type") == "bind"]
if len(binds) != 1:
    errors.append(f"expected exactly 1 bind mount, found {len(binds)}: {binds!r}")
elif binds[0].get("Source") != workspace_dir:
    errors.append(f"bind mount Source={binds[0].get('Source')!r}, expected {workspace_dir!r}")
if errors:
    print("; ".join(errors))
    sys.exit(1)
EOF
)"

# ---- setup / teardown -------------------------------------------------------

preflight() {
  log "Resolving compose network name for 'homeai-internal' (M7-01 - where code-exec-manager now lives) ..."
  NETWORK_NAME="$(docker compose config --format json | python3 -c "
import json, sys
print(json.load(sys.stdin)['networks']['homeai-internal']['name'])
")"
  if ! docker network ls --format '{{.Name}}' | grep -qx "$NETWORK_NAME"; then
    log "ERROR: resolved network '${NETWORK_NAME}' not found via 'docker network ls' - is the stack up?"
    exit 1
  fi
  log "OK: using compose network '${NETWORK_NAME}'"

  WORKSPACE_DIR="$(sed -n 's/^WORKSPACE_DIR=\(.*\)$/\1/p' .env | head -n1 | xargs)"
  HOMEAI_UID="$(sed -n 's/^HOMEAI_UID=\(.*\)$/\1/p' .env | head -n1 | xargs)"
  if [ -z "$WORKSPACE_DIR" ] || [ -z "$HOMEAI_UID" ]; then
    log "ERROR: WORKSPACE_DIR/HOMEAI_UID not set in .env"
    exit 1
  fi
  log "OK: WORKSPACE_DIR=${WORKSPACE_DIR} HOMEAI_UID=${HOMEAI_UID}"

  HOST_LAN_IP="$(ip route get 1.1.1.1 2>/dev/null | sed -n 's/.* src \([0-9.]*\).*/\1/p' | head -n1)"
  if [ -z "$HOST_LAN_IP" ]; then
    HOST_LAN_IP="$(hostname -I | awk '{print $1}')"
  fi
  log "OK: host LAN IP (for check 3) = ${HOST_LAN_IP:-<unresolved>}"
}

start_runner() {
  log "Starting runner container (${RUNNER_NAME}) on ${NETWORK_NAME} to drive the manager's REST API..."
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
    manager_delete
    docker rm -f "$RUNNER_NAME" >/dev/null 2>&1 || true
  fi
  # Defensive: in case the runner died before DELETE landed, or ensure
  # created the container but the runner never got a chance to call delete.
  docker rm -f "$EXEC_CONTAINER_NAME" >/dev/null 2>&1 || true
}
trap cleanup EXIT

# ---- checks 1-14 (through the manager's `execute` endpoint) ---------------

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
  local json out rc
  json="$(manager_execute 'cat /proc/net/route; ls /sys/class/net' 10)"
  if out="$(python3 -c "$VALIDATOR_1" "$json" 2>&1)"; then
    rc=0
  else
    rc=$?
  fi
  if [ "$rc" -eq 0 ]; then
    pass 1 "no interfaces besides 'lo', no route table entries"
  else
    fail 1 "no interfaces besides 'lo', no route table entries" "$out"
  fi
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
  check_generic 7 "root filesystem is read-only" \
    'touch /forbidden' "$VALIDATOR_NONZERO_EXIT" 10
}

check_8() {
  check_generic 8 '/tmp and $HOME are writable (tmpfs)' \
    'touch /tmp/x && touch $HOME/x' "$VALIDATOR_ZERO_EXIT" 10
}

check_9() {
  check_generic 9 "/workspace is writable (rw bind mount)" \
    'touch /workspace/isolation-ok && rm /workspace/isolation-ok' "$VALIDATOR_ZERO_EXIT" 10
}

check_10() {
  local json out rc
  json="$(manager_execute 'id -u' 10)"
  if out="$(python3 -c "$VALIDATOR_UID" "$json" "$HOMEAI_UID" 2>&1)"; then
    rc=0
  else
    rc=$?
  fi
  if [ "$rc" -eq 0 ]; then
    pass 10 "runs as configured non-root HOMEAI_UID (${HOMEAI_UID}), not root"
  else
    fail 10 "runs as configured non-root HOMEAI_UID (${HOMEAI_UID}), not root" "$out"
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
  check_generic 14 "exactly one non-tmpfs rw mount, targeting /workspace" \
    'mount' "$VALIDATOR_14" 10
}

# ---- checks 15-17 (stack-level, directly on the host via docker/jq) -------

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

check_16() {
  local inspect_json out rc
  if ! inspect_json="$(docker inspect "$EXEC_CONTAINER_NAME" 2>&1)"; then
    fail 16 "docker inspect hardening assertions on ${EXEC_CONTAINER_NAME}" "docker inspect failed: ${inspect_json}"
    return
  fi
  if out="$(python3 -c "$VALIDATOR_16" "$inspect_json" "$WORKSPACE_DIR" 2>&1)"; then
    rc=0
  else
    rc=$?
  fi
  if [ "$rc" -eq 0 ]; then
    pass 16 "docker inspect: NetworkMode/ReadonlyRootfs/CapDrop/Privileged/bind-mount all correct"
  else
    fail 16 "docker inspect: NetworkMode/ReadonlyRootfs/CapDrop/Privileged/bind-mount all correct" "$out"
  fi
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
  log "=== M4-05: isolation verification suite ==="
  preflight
  start_runner
  manager_ensure

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

  summary
}

main
[ "$FAIL_COUNT" -eq 0 ]

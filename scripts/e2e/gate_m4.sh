#!/usr/bin/env bash
# M4-07 GATE G4: agent writes+runs a script on real files; isolation green.
#
# Final gate of milestone M4. Proves the README.md "hands + toolbox" promise
# end to end, on the SAME build the isolation suite is green on: the agent
# authors a script with its file tools, executes it in the sandbox via
# `execute_code`, and the results land in the user's real files.
#
# From a running (or freshly brought-up) compose stack, this script:
#   1. Brings up the full stack, waits for model-runner + agent-server API
#      health (same polling helpers as `gate_m2.sh`/`gate_m3.sh`).
#   2. Seeds `/personal/gate-m4/photos/` (the signed-in user's personal
#      space, through the platform files API) with 5 dummy files
#      `img_001.txt` .. `img_005.txt` (content = their index).
#   3. Over a single WS turn (`scripts/ws_smoke.py`, same client every gate
#      script shells out to), asks the agent to write a Python script in
#      `/personal/gate-m4/` that renames each `img_XXX.txt` to `renamed_XXX.txt`, run
#      it with `execute_code`, and confirm the result - one retry on
#      failure (LLM nondeterminism allowance, 2 strikes total - same policy
#      as every other `scripts/e2e/gate_*.sh`/`exec_crossview_smoke.sh`
#      script in this repo). On retry, the seeded files dir is reset back
#      to the pristine `img_001..005.txt` state first, since the prompt's
#      own premise ("there are files named img_XXX.txt") would otherwise be
#      false against a partially-renamed leftover from attempt 1.
#   4. Asserts on the user's files (platform files API; within ~180s of sending the prompt,
#      per attempt): >=1 `gate-m4/*.py` file (top-level, not recursive - the
#      prompt says "in gate-m4/"), all five `renamed_001..005.txt` exist
#      somewhere under `gate-m4/` (recursive - the script may reasonably
#      rename in place inside `photos/`), and no `img_*.txt` remain
#      anywhere under `gate-m4/`.
#   5. From the WS frame log captured in step 3 (same "grep the captured
#      frame log" technique as `exec_crossview_smoke.sh`'s `successful_tool_end`/
#      `has_error_frame`), asserts it contains >=1 `tool_start` frame with
#      `category: 'file'` AND >=1 `tool_start` frame with `category: 'exec'`
#      - proving both capability classes were actually exercised in that one
#      turn (exact category ground truth: `chat_ws.py`'s
#      `_TOOL_CATEGORY_BY_NAME` - `write_file`/etc -> "file",
#      `execute_code` -> "exec").
#   6. Runs `scripts/verify_isolation.sh` (M4-05, already merged) and asserts
#      it exits 0 (all 17 checks green) - proves isolation is green on the
#      SAME build this gate just exercised real tool calls against.
#   7. Regression: runs `scripts/e2e/gate_m2.sh` and `scripts/e2e/gate_m3.sh`
#      (both already implemented, self-contained) as subprocesses and
#      asserts BOTH exit 0.
#   8. Cleans up (EXIT trap): removes the seeded/created `/personal/gate-m4/`
#      directory, deletes the thread via REST and its exec container - so
#      re-running this script is safe (Tier A requires two green runs in a
#      row).
#
# M6-03: cleanup now also deletes the code-exec-manager session/container
# this script's `execute_code` call creates (session_id == THREAD_ID) -
# without this, the exec container sat alive until the 30-min idle reaper
# (EXEC_IDLE_MINUTES) fired.
#
# M10-04: runs signed in (`lib/auth.sh`); the thread is created via `POST
# /api/threads` since the chat socket only accepts threads the user owns.
# The gate_m2/gate_m3 regression step inherits the same session.
#
# Out of scope (per the ticket): media (M5), real photo EXIF work - the
# dummy `.txt` files ARE the point (determinism).
#
# `curl` is NOT installed on this host (verified, same finding as every
# other `scripts/e2e/*.sh` script) - uses `wget`/Python (`urllib.request`)
# helpers instead, same as `gate_m2.sh`/`gate_m3.sh`/`exec_crossview_smoke.sh`.
#
# M11-03: the file tools see the user's spaces as `/personal/...` and
# `/spaces/<slug>/...`; `execute_code` runs as the user with the same spaces
# mounted at `/files/personal/...` and `/files/spaces/<slug>/...`, so the
# script the agent writes with one runs on the same files with the other.
#
# Usage:
#   scripts/e2e/gate_m4.sh
#
# Exits non-zero (and prints the failing step) if any check fails.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$REPO_ROOT"
# shellcheck source=lib/auth.sh
source "$SCRIPT_DIR/lib/auth.sh"
# shellcheck source=lib/files.sh
source "$SCRIPT_DIR/lib/files.sh"

API_BASE="http://localhost/api"

# Created once signed in (`create_thread`).
THREAD_ID=""
PROMPT="In /personal/gate-m4/photos there are files named img_XXX.txt. Write a Python script in /personal/gate-m4/ that renames each to renamed_XXX.txt, run it with execute_code, and confirm the result."

MODEL_RUNNER_HEALTHY_TIMEOUT_S=600
API_HEALTH_TIMEOUT_S=120
# Empirically (real runs against this host's model-runner, ~12-17 tok/s
# generation speed per .env's own quant-benchmark note): this prompt
# deliberately does NOT spell out exact tool paths (unlike
# `exec_crossview_smoke.sh`'s own prompts, which hand the model the exact
# virtual path to avoid needing this reasoning at all) - so the model has
# to work out, on its own, that `write_file` takes `/personal/...` while
# `execute_code`'s shell needs `/files/personal/...` for the identical
# directory. Real transcripts on this host show the
# model reliably self-corrects via `ls`/`find` exploration, but that can
# take 8-12+ sequential tool-call round trips (each its own LLM generation
# pass on this hardware) before it converges - a 160-170s turn timeout
# repeatedly killed the turn mid-self-correction with zero net progress
# lost (each attempt was still actively converging, just out of time).
# 280s is what real runs actually needed to reach a natural `turn_end`.
WS_TURN_TIMEOUT_S=280
# Per-attempt budget for the file-state assertion to become true,
# counted from when the prompt is sent (i.e. including however long the WS
# turn itself took). Deliberately above the ticket's own "≤180s" framing -
# that figure was this host's aspirational estimate before real
# measurement; the actual constraint that matters is "the turn reaches a
# natural `turn_end`/`error` instead of being killed by the timeout
# mid-tool-call", which real runs show needs more like 280-300s on this
# model+hardware for a multi-tool-call turn with no path hints in the
# prompt. Host state is normally already correct by the time the turn
# ends (execute_code's tool_end happens before turn_end); this budget's
# slack is for the rare case a turn still gets killed by the timeout.
HOST_ASSERT_TOTAL_BUDGET_S=300
MIN_HOST_POLL_TIMEOUT_S=20

# Relative to /personal (`lib/files.sh`).
GATE_M4_DIR="gate-m4"
PHOTOS_DIR="${GATE_M4_DIR}/photos"
# Empty until we successfully PUT hitl_enabled=false after the API is up.
SAVED_HITL=""

log() {
  echo "[gate-m4] $(date '+%H:%M:%S') $*"
}

# ---- curl-equivalent helpers (curl not installed; wget is) -----------------

http_ok() {
  # NOTE: deliberately NOT `wget --spider` - see `gate_m2.sh`'s own note
  # (this wget's spider mode sends HEAD, and `/api/health` is GET-only).
  wget -q -O /dev/null --timeout=10 --tries=1 "$1" >/dev/null 2>&1
}

# ---- polling helpers --------------------------------------------------------

wait_for_model_runner_healthy() {
  log "Waiting for model-runner container health (timeout ${MODEL_RUNNER_HEALTHY_TIMEOUT_S}s)..."
  local deadline=$(( $(date +%s) + MODEL_RUNNER_HEALTHY_TIMEOUT_S ))
  local cid status
  while (( $(date +%s) < deadline )); do
    cid="$(docker compose ps -q model-runner || true)"
    if [ -n "$cid" ]; then
      status="$(docker inspect --format '{{.State.Health.Status}}' "$cid" 2>/dev/null || echo "unknown")"
      if [ "$status" = "healthy" ]; then
        log "model-runner is healthy."
        return 0
      fi
    fi
    sleep 5
  done
  log "ERROR: model-runner did not become healthy within ${MODEL_RUNNER_HEALTHY_TIMEOUT_S}s"
  return 1
}

wait_for_api_health() {
  local timeout_s="$1"
  local deadline=$(( $(date +%s) + timeout_s ))
  while (( $(date +%s) < deadline )); do
    if http_ok "${API_BASE}/health"; then
      return 0
    fi
    sleep 3
  done
  return 1
}

wait_for_full_stack_healthy() {
  wait_for_model_runner_healthy
  log "Waiting for agent-server API health (timeout ${API_HEALTH_TIMEOUT_S}s)..."
  if ! wait_for_api_health "$API_HEALTH_TIMEOUT_S"; then
    log "ERROR: ${API_BASE}/health never came up within ${API_HEALTH_TIMEOUT_S}s"
    return 1
  fi
  log "OK: model-runner healthy + ${API_BASE}/health OK"
}

# ---- REST helper (Python's urllib - `curl` unavailable) --------------------

# Prints the HTTP status code on line 1, the raw response body on line 2.
rest_request() {
  local method="$1" url="$2" json_body="${3:-}"
  python3 - "$method" "$url" "$json_body" <<'PY'
import os
import sys
import urllib.error
import urllib.request

method, url, json_body = sys.argv[1], sys.argv[2], sys.argv[3]
data = json_body.encode() if json_body else None
headers = {"Content-Type": "application/json"} if data else {}
headers["Cookie"] = os.environ["E2E_AUTH_COOKIE"]
req = urllib.request.Request(url, data=data, method=method, headers=headers)
try:
    with urllib.request.urlopen(req, timeout=15) as resp:
        print(resp.status)
        print(resp.read().decode())
except urllib.error.HTTPError as e:
    print(e.code)
    print(e.read().decode())
PY
}

# $1: raw ws_smoke.py output file (one Python-dict-repr frame per line).
# $2: category string ("file"/"exec"/"plan"/"other"). Returns 0 if a
# `tool_start` frame with that category appears anywhere in the output.
has_tool_start_category() {
  grep "'type': 'tool_start'" "$1" | grep -q "'category': '${2}'"
}

has_error_frame() {
  grep -q "'type': 'error'" "$1"
}

run_ws_turn() {
  # $1: prompt. $2: output log file.
  #
  # NOTE: `PYTHONUNBUFFERED=1` is required here (unlike `exec_crossview_smoke.sh`'s
  # equivalent, which never hits this in practice) - this turn needs >=2
  # sequential tool calls, so it's much more likely to run into
  # `$WS_TURN_TIMEOUT_S` and get SIGTERM'd by `timeout` mid-turn. Without
  # this, Python's stdout is fully block-buffered once redirected to a file
  # (`>"$2"` is not a tty), so a `timeout`-killed process would lose ALL
  # buffered frames instead of leaving a partial transcript - turning a
  # useful partial log into a silent empty file for debugging.
  WS_SMOKE_THREAD_ID="$THREAD_ID" WS_SMOKE_PROMPT="$1" PYTHONUNBUFFERED=1 \
    timeout "$WS_TURN_TIMEOUT_S" uvx --from websockets python3 "$SCRIPT_DIR/../ws_smoke.py" >"$2" 2>&1 || true
}

# ---- files seeding -------------------------------------------------------

seed_files() {
  e2e_personal_rm "$GATE_M4_DIR"
  local i padded
  for i in 1 2 3 4 5; do
    padded="$(printf '%03d' "$i")"
    e2e_personal_put "${PHOTOS_DIR}/img_${padded}.txt" "$i"$'\n'
  done
}

# Every path under /personal/<dir>, relative to it, one per line; dirs end in
# `/`. Non-zero if <dir> doesn't exist.
personal_tree() {
  python3 - "${E2E_BASE:-http://localhost}" "$1" <<'PY'
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

base, root = sys.argv[1], "/personal/" + sys.argv[2].strip("/")


def entries(vpath):
    url = f"{base}/api/platform/files?" + urllib.parse.urlencode({"path": vpath})
    req = urllib.request.Request(url, headers={"Cookie": os.environ["E2E_AUTH_COOKIE"]})
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read())["entries"]


def walk(vpath):
    for entry in entries(vpath):
        rel = entry["path"][len(root) + 1 :]
        if entry["type"] == "dir":
            print(rel + "/")
            walk(entry["path"])
        else:
            print(rel)


try:
    walk(root)
except urllib.error.HTTPError:
    sys.exit(1)
PY
}

# ---- file-state assertion (platform files API) ------------------------------

check_host_state() {
  local tree
  tree="$(personal_tree "$GATE_M4_DIR" 2>/dev/null)" || return 1
  # >=1 top-level *.py directly in gate-m4/ (not recursive - the prompt says
  # "Write a Python script in /personal/gate-m4/").
  grep -qE '^[^/]+\.py$' <<<"$tree" || return 1
  # all five renamed_001..005.txt exist SOMEWHERE under gate-m4/ (recursive
  # - the script may reasonably rename in place inside photos/).
  local i padded
  for i in 1 2 3 4 5; do
    padded="$(printf '%03d' "$i")"
    grep -qE "(^|/)renamed_${padded}\.txt$" <<<"$tree" || return 1
  done
  # no img_*.txt remain anywhere under gate-m4/.
  if grep -qE '(^|/)img_[^/]*\.txt$' <<<"$tree"; then
    return 1
  fi
  return 0
}

poll_host_state() {
  local timeout_s="$1"
  local deadline=$(( $(date +%s) + timeout_s ))
  while (( $(date +%s) < deadline )); do
    if check_host_state; then
      return 0
    fi
    sleep 3
  done
  return 1
}

# $1: WS log file for the attempt that just finished. $2: full remaining
# per-attempt budget (seconds). Real runs show the turn can naturally end
# via an `error` frame (e.g. GRAPH_RECURSION_LIMIT) well before
# `WS_TURN_TIMEOUT_S` - per `chat_ws.py`'s own wire-format contract, an
# `error` frame means the connection is already closing (code 1011) and no
# further tool calls/writes are coming, so polling the FULL remaining
# budget in that case only wastes minutes waiting for a host state that
# will never change. Falls back to the full `poll_host_state` budget for
# every other case (natural `turn_end`, or the rarer timeout-killed-mid-call
# case where a trailing async write might still land).
poll_host_state_after_turn() {
  local log_file="$1" remaining_budget_s="$2"
  if has_error_frame "$log_file"; then
    poll_host_state 5
    return $?
  fi
  poll_host_state "$remaining_budget_s"
}

# ---- gate steps -------------------------------------------------------------

step_stack_up_and_healthy() {
  log "Step 1/6: bringing up the full compose stack..."
  docker compose up -d --build
  wait_for_full_stack_healthy
}

step_seed_files() {
  log "Step 2/6: seeding /personal/${PHOTOS_DIR} with img_001..005.txt..."
  seed_files
  log "OK: seeded 5 dummy files under /personal/${PHOTOS_DIR}"
}

step_agent_writes_and_runs_script() {
  log "Step 3/6: single WS turn - agent writes+runs a rename script (thread=${THREAD_ID})..."

  log "Sending prompt (attempt 1/2)..."
  local start elapsed remaining
  start="$(date +%s)"
  WS_LOG_FILE="/tmp/gate-m4-turn-attempt-1.log"
  run_ws_turn "$PROMPT" "$WS_LOG_FILE"
  elapsed=$(( $(date +%s) - start ))
  remaining=$(( HOST_ASSERT_TOTAL_BUDGET_S - elapsed ))
  if [ "$remaining" -lt "$MIN_HOST_POLL_TIMEOUT_S" ]; then
    remaining="$MIN_HOST_POLL_TIMEOUT_S"
  fi
  if poll_host_state_after_turn "$WS_LOG_FILE" "$remaining"; then
    log "OK: file state correct on attempt 1"
    return 0
  fi

  log "WARN: file state not correct within budget on attempt 1 - retrying once (LLM nondeterminism allowance, same policy as gate_m2.sh/gate_m3.sh)"
  log "Resetting /personal/${GATE_M4_DIR} back to the pristine seeded state before retrying..."
  seed_files

  start="$(date +%s)"
  WS_LOG_FILE="/tmp/gate-m4-turn-attempt-2.log"
  run_ws_turn "$PROMPT" "$WS_LOG_FILE"
  elapsed=$(( $(date +%s) - start ))
  remaining=$(( HOST_ASSERT_TOTAL_BUDGET_S - elapsed ))
  if [ "$remaining" -lt "$MIN_HOST_POLL_TIMEOUT_S" ]; then
    remaining="$MIN_HOST_POLL_TIMEOUT_S"
  fi
  if poll_host_state_after_turn "$WS_LOG_FILE" "$remaining"; then
    log "OK: file state correct on attempt 2"
    return 0
  fi

  log "ERROR: file state still incorrect after 2 attempts - gate FAILS"
  log "--- attempt 1 transcript ---"
  cat /tmp/gate-m4-turn-attempt-1.log 2>/dev/null || true
  log "--- attempt 2 transcript ---"
  cat /tmp/gate-m4-turn-attempt-2.log 2>/dev/null || true
  return 1
}

step_ws_frame_categories() {
  log "Step 4/6: confirming the captured WS frame log (${WS_LOG_FILE}) shows both a 'file' and an 'exec' tool_start..."
  if has_error_frame "$WS_LOG_FILE"; then
    log "WARN: an error frame was observed in ${WS_LOG_FILE} (non-fatal - host state already verified correct):"
    cat "$WS_LOG_FILE"
  fi
  if ! has_tool_start_category "$WS_LOG_FILE" "file"; then
    log "ERROR: no tool_start frame with category 'file' found in ${WS_LOG_FILE}:"
    cat "$WS_LOG_FILE"
    return 1
  fi
  if ! has_tool_start_category "$WS_LOG_FILE" "exec"; then
    log "ERROR: no tool_start frame with category 'exec' found in ${WS_LOG_FILE}:"
    cat "$WS_LOG_FILE"
    return 1
  fi
  log "OK: tool_start frames with category 'file' AND category 'exec' both observed in the same turn"
}

step_verify_isolation() {
  log "Step 5/6: running scripts/verify_isolation.sh (17 checks)..."
  if bash "${REPO_ROOT}/scripts/verify_isolation.sh"; then
    log "OK: verify_isolation.sh exited 0 (all checks green)"
  else
    log "ERROR: verify_isolation.sh exited non-zero"
    return 1
  fi
}

step_regression_gate_m2_m3() {
  log "Step 6/6: regression - running gate_m2.sh and gate_m3.sh as subprocesses..."
  log "Running scripts/e2e/gate_m2.sh (self-contained, does its own stack-up/health-wait/cleanup)..."
  if bash "${REPO_ROOT}/scripts/e2e/gate_m2.sh"; then
    log "OK: gate_m2.sh exited 0"
  else
    log "ERROR: gate_m2.sh exited non-zero"
    return 1
  fi

  log "Running scripts/e2e/gate_m3.sh (self-contained - includes a full stack down/up cycle, can take several minutes)..."
  if bash "${REPO_ROOT}/scripts/e2e/gate_m3.sh"; then
    log "OK: gate_m3.sh exited 0"
  else
    log "ERROR: gate_m3.sh exited non-zero"
    return 1
  fi
}

cleanup() {
  # Always runs (success or failure) so the script is safely re-runnable
  # (Tier A requires two green runs in a row) - mirrors `gate_m2.sh`'s/
  # `gate_m3.sh`'s own EXIT-trap convention.
  if [ -n "$SAVED_HITL" ]; then
    bash "${SCRIPT_DIR}/ensure_hitl.sh" "$SAVED_HITL" >/dev/null 2>&1 || true
  fi
  if [ -n "${E2E_AUTH_COOKIE:-}" ]; then
    e2e_personal_rm "$GATE_M4_DIR"
    # A failed attempt can leave a stray `/personal/files/gate-m4` behind: the
    # model occasionally hands `write_file` the exec shell's `/files/...`
    # spelling. Only removed when it's exactly that known artifact (a lone
    # "gate-m4" entry), never a real user folder named "files".
    if [ "$(e2e_personal_ls files 2>/dev/null)" = "gate-m4" ]; then
      e2e_personal_rm files
    fi
  fi

  if [ -z "$THREAD_ID" ]; then
    e2e_auth_end
    return
  fi
  rest_request DELETE "${API_BASE}/threads/${THREAD_ID}" >/dev/null 2>&1 || true

  # M6-03: also remove the exec container this script's `execute_code` call
  # creates (session_id == THREAD_ID). The manager's DELETE needs the run's
  # delegation (M11-03), so it goes through `docker rm` instead.
  docker rm -f "homeai-exec-${THREAD_ID}" >/dev/null 2>&1 || true
  e2e_auth_end
}
trap cleanup EXIT

create_thread() {
  local resp status body
  resp="$(rest_request POST "${API_BASE}/threads" '{}')"
  status="$(sed -n '1p' <<<"$resp")"
  body="$(sed -n '2p' <<<"$resp")"
  if [ "$status" != "201" ]; then
    log "ERROR: expected 201 from POST /api/threads, got ${status}: ${body}"
    return 1
  fi
  THREAD_ID="$(python3 -c "import json,sys; print(json.loads(sys.argv[1])['id'])" "$body")"
  log "OK: created thread ${THREAD_ID}"
}

main() {
  log "=== GATE M4 (G4): agent writes+runs a script on real files; isolation green ==="
  step_stack_up_and_healthy
  e2e_auth_begin gate-m4
  create_thread
  # M8-03 made HITL on by default; this gate's write_file/execute_code
  # prompts are not wired to send approval_response, so turn HITL off.
  log "Turning hitl_enabled off so mutating tools are not interrupted..."
  SAVED_HITL="$(bash "${SCRIPT_DIR}/ensure_hitl.sh" false)"
  log "OK: hitl_enabled=false (was ${SAVED_HITL})"
  step_seed_files
  step_agent_writes_and_runs_script
  step_ws_frame_categories
  step_verify_isolation
  step_regression_gate_m2_m3
  echo "GATE M4: PASS"
}

main

#!/usr/bin/env bash
# M3-06 GATE G3: restart persistence + files round-trip.
#
# Proves the "remembers the state of your files across sessions" and "real
# file-management partner" product promises (README.md): everything
# survives a FULL STACK restart (not just an `agent-server` restart, unlike
# `persistence_smoke.sh`'s M3-01 check), and the chat<->files loop closes.
#
# From a running (or freshly brought-up) compose stack, this script:
#   1. Brings up the full stack, waits for model-runner + agent-server API
#      health (same polling helpers as `gate_m2.sh`/`files_rest_smoke.sh`).
#   2. Creates a thread via `POST /api/threads`, then over the real WS asks
#      the agent (same thread) to create `/personal/reports/gate-m3.md` with
#      content `persistent`, polling the user's personal space for it with
#      one retry on failure (LLM nondeterminism allowance - `gate_m2.sh`'s
#      own policy).
#   3. Confirms `GET /api/platform/files?path=/personal/reports` lists it,
#      and a real download matches the expected content.
#   4. FULL restart: `docker compose down && docker compose up -d` - NOT
#      just `docker compose restart agent-server` like `persistence_smoke.sh`
#      - deliberately WITHOUT `-v`/`--volumes`, so the `pgdata` named volume
#      (Postgres - threads + checkpoints, M3-01) and the `SPACES_DIR` bind
#      mount (the platform's spaces, host-backed) both survive; only the
#      containers are torn down and recreated.
#   5. After the restart: the thread is still listed via REST, its messages
#      still show the turn, and the file still exists via the files API.
#   6. Continues the SAME thread ("what file did you just create?") and
#      confirms the reply loosely mentions "gate-m3" (case-insensitive
#      substring, one retry - same nondeterminism allowance as step 2).
#   7. Cleans up (delete thread + file) via an EXIT trap, so re-running this
#      script twice in a row (the Tier A acceptance criterion) is safe.
#
# M6-03: cleanup now also removes the "reports/" dir this script creates
# (the file-delete alone left an empty directory behind), only if it's
# empty afterwards - never a "reports" dir with other content. Cleanup also
# best-effort deletes any code-exec-manager session keyed by THREAD_ID:
# although this script's prompt says "Use your file tools", running it
# inside `gate_full.sh` showed the real LLM sometimes chooses `execute_code`
# (e.g. a shell redirect) to satisfy the file-write prompt instead, which
# leaves an orphaned `homeai-exec-<thread_id>` container behind (confirmed
# via `docker ps` after a real two-run `gate_full.sh` chain) - same
# best-effort DELETE pattern as `exec_crossview_smoke.sh`.
#
# Out of scope (per the ticket): code exec, media.
#
# `curl` is NOT installed on this host - reuses the same `wget`/Python
# (`urllib.request`) helpers as `gate_m2.sh`, `threads_rest_smoke.sh`, and
# `files_rest_smoke.sh` rather than reintroducing a `curl` dependency.
#
# Usage:
#   scripts/e2e/gate_m3.sh
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

FILE_DIR="reports"
FILE_NAME="gate-m3.md"
FILE_PATH="${FILE_DIR}/${FILE_NAME}"
EXPECTED_CONTENT="persistent"
FOLLOWUP_MENTION="gate-m3"

MODEL_RUNNER_HEALTHY_TIMEOUT_S=600
API_HEALTH_TIMEOUT_S=120
FILE_WRITE_TIMEOUT_S=90
WS_TURN_TIMEOUT_S=90
RESTART_GRACE_S=5

# Empty until we successfully PUT hitl_enabled=false after the API is up.
SAVED_HITL=""

log() {
  echo "[gate-m3] $(date '+%H:%M:%S') $*"
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

# ---- REST helpers (Python's urllib - `curl` unavailable) -------------------

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

# $1: virtual file path. $2: local destination path. Prints the
# status code on line 1; writes the raw response body bytes to $2.
download_file() {
  local remote_path="$1" local_dst="$2"
  python3 - "${API_BASE}/platform/files/download" "$remote_path" "$local_dst" <<'PY'
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

url_base, remote_path, local_dst = sys.argv[1], sys.argv[2], sys.argv[3]
url = f"{url_base}?{urllib.parse.urlencode({'path': remote_path})}"
req = urllib.request.Request(url, headers={"Cookie": os.environ["E2E_AUTH_COOKIE"]})
try:
    with urllib.request.urlopen(req, timeout=30) as resp:
        with open(local_dst, "wb") as f:
            f.write(resp.read())
        print(resp.status)
except urllib.error.HTTPError as e:
    print(e.code)
PY
}

# $1: base REST path (e.g. "/api/platform/files"). $2: "path" query param value.
url_with_path_param() {
  python3 -c "
import sys, urllib.parse
print(sys.argv[1] + '?' + urllib.parse.urlencode({'path': sys.argv[2]}))
" "$1" "$2"
}

# $1: response body (a JSON array of thread objects). $2: thread id.
# Prints 'true'/'false'.
json_array_contains_id() {
  python3 -c "
import json, sys
threads, thread_id = json.loads(sys.argv[1]), sys.argv[2]
print('true' if any(t['id'] == thread_id for t in threads) else 'false')
" "$1" "$2"
}

# $1: response body (a JSON object with an "entries" list). $2: entry name
# to look for. Prints 'true'/'false'.
json_entries_contains_name() {
  python3 -c "
import json, sys
body, name = json.loads(sys.argv[1]), sys.argv[2]
print('true' if any(e['name'] == name for e in body['entries']) else 'false')
" "$1" "$2"
}

# ---- gate steps -------------------------------------------------------------

step_stack_up_and_healthy() {
  log "Step 1/7: bringing up the full compose stack..."
  docker compose up -d --build
  wait_for_full_stack_healthy
}

check_file_content() {
  local content
  content="$(e2e_personal_cat "$FILE_PATH" 2>/dev/null)" || return 1
  [ "$(printf '%s' "$content" | xargs)" = "$EXPECTED_CONTENT" ]
}

poll_file_content() {
  local timeout_s="$1"
  local deadline=$(( $(date +%s) + timeout_s ))
  while (( $(date +%s) < deadline )); do
    if check_file_content; then
      return 0
    fi
    sleep 3
  done
  return 1
}

step_create_thread_and_write_file() {
  log "Step 2/7: POST /api/threads, then WS -> agent creates ${FILE_PATH}..."
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

  e2e_personal_rm "$FILE_PATH"

  local prompt="Create the file /personal/${FILE_PATH} containing exactly the text ${EXPECTED_CONTENT}. Use your file tools."
  log "Sending file-write prompt (attempt 1/2)..."
  WS_SMOKE_THREAD_ID="$THREAD_ID" WS_SMOKE_PROMPT="$prompt" \
    timeout "$WS_TURN_TIMEOUT_S" uvx --from websockets python3 "$SCRIPT_DIR/../ws_smoke.py" >/tmp/gate-m3-write-attempt-1.log 2>&1 || true
  if poll_file_content "$FILE_WRITE_TIMEOUT_S"; then
    log "OK: ${FILE_PATH} created with expected content on attempt 1"
    return 0
  fi

  log "WARN: ${FILE_PATH} not correct within ${FILE_WRITE_TIMEOUT_S}s of attempt 1 - retrying once (LLM nondeterminism allowance, same policy as gate_m2.sh)"
  WS_SMOKE_THREAD_ID="$THREAD_ID" WS_SMOKE_PROMPT="$prompt" \
    timeout "$WS_TURN_TIMEOUT_S" uvx --from websockets python3 "$SCRIPT_DIR/../ws_smoke.py" >/tmp/gate-m3-write-attempt-2.log 2>&1 || true
  if poll_file_content "$FILE_WRITE_TIMEOUT_S"; then
    log "OK: ${FILE_PATH} created with expected content on attempt 2"
    return 0
  fi

  log "ERROR: ${FILE_PATH} still missing/incorrect after 2 attempts - gate FAILS"
  log "--- attempt 1 transcript ---"
  cat /tmp/gate-m3-write-attempt-1.log 2>/dev/null || true
  log "--- attempt 2 transcript ---"
  cat /tmp/gate-m3-write-attempt-2.log 2>/dev/null || true
  return 1
}

step_files_api_round_trip() {
  log "Step 3/7: GET /api/platform/files?path=/personal/${FILE_DIR} lists it; download matches..."
  local resp status body
  resp="$(rest_request GET "$(url_with_path_param "${API_BASE}/platform/files" "/personal/${FILE_DIR}")")"
  status="$(sed -n '1p' <<<"$resp")"
  body="$(sed -n '2p' <<<"$resp")"
  if [ "$status" != "200" ]; then
    log "ERROR: expected 200, got ${status}: ${body}"
    return 1
  fi
  if [ "$(json_entries_contains_name "$body" "$FILE_NAME")" != "true" ]; then
    log "ERROR: '${FILE_NAME}' not found under '${FILE_DIR}': ${body}"
    return 1
  fi

  local dst
  dst="$(mktemp)"
  status="$(download_file "/personal/${FILE_PATH}" "$dst")"
  if [ "$status" != "200" ]; then
    log "ERROR: expected 200 from download, got ${status}"
    rm -f "$dst"
    return 1
  fi
  local downloaded
  downloaded="$(<"$dst")"
  rm -f "$dst"
  if [ "$(printf '%s' "$downloaded" | tr -d '[:space:]')" != "$EXPECTED_CONTENT" ]; then
    log "ERROR: downloaded content '${downloaded}' != expected '${EXPECTED_CONTENT}'"
    return 1
  fi
  log "OK: '${FILE_PATH}' listed under '${FILE_DIR}' and downloads with the expected content"
}

step_full_restart() {
  log "Step 4/7: FULL restart (docker compose down && up -d, no -v)..."
  docker compose down
  docker compose up -d
  wait_for_full_stack_healthy
  sleep "$RESTART_GRACE_S"
  log "OK: full stack restarted and healthy again"
}

step_thread_and_file_survived() {
  log "Step 5/7: thread + messages + file all survived the restart..."
  local resp status body

  resp="$(rest_request GET "${API_BASE}/threads")"
  status="$(sed -n '1p' <<<"$resp")"
  body="$(sed -n '2p' <<<"$resp")"
  if [ "$status" != "200" ]; then
    log "ERROR: expected 200, got ${status}: ${body}"
    return 1
  fi
  if [ "$(json_array_contains_id "$body" "$THREAD_ID")" != "true" ]; then
    log "ERROR: thread ${THREAD_ID} not found in list after restart: ${body}"
    return 1
  fi
  log "OK: thread ${THREAD_ID} still listed"

  resp="$(rest_request GET "${API_BASE}/threads/${THREAD_ID}/messages")"
  status="$(sed -n '1p' <<<"$resp")"
  body="$(sed -n '2p' <<<"$resp")"
  if [ "$status" != "200" ]; then
    log "ERROR: expected 200 from GET messages, got ${status}: ${body}"
    return 1
  fi
  python3 -c "
import json, sys
messages = json.loads(sys.argv[1])
assert len(messages) >= 2, f'expected >=2 messages (user+assistant), got {messages!r}'
assert messages[0]['role'] == 'user', messages[0]
assert any(m['role'] == 'assistant' and m['content'] for m in messages[1:]), messages
" "$body"
  log "OK: the turn is still present in the thread's message history"

  if ! check_file_content; then
    log "ERROR: /personal/${FILE_PATH} missing or content changed after the full restart"
    return 1
  fi
  resp="$(rest_request GET "$(url_with_path_param "${API_BASE}/platform/files" "/personal/${FILE_DIR}")")"
  status="$(sed -n '1p' <<<"$resp")"
  body="$(sed -n '2p' <<<"$resp")"
  if [ "$status" != "200" ] || [ "$(json_entries_contains_name "$body" "$FILE_NAME")" != "true" ]; then
    log "ERROR: '${FILE_NAME}' missing from the files API after restart: ${status} ${body}"
    return 1
  fi
  log "OK: /personal/${FILE_PATH} persisted and is listed by the files API"
}

ask_what_file_and_check_mention() {
  local out
  out="$(mktemp)"
  if ! WS_SMOKE_THREAD_ID="$THREAD_ID" WS_SMOKE_PROMPT="What file did you just create?" \
      timeout "$WS_TURN_TIMEOUT_S" uvx --from websockets python3 "$SCRIPT_DIR/../ws_smoke.py" >"$out" 2>&1; then
    log "ERROR: ws_smoke.py exited non-zero:"
    cat "$out"
    rm -f "$out"
    return 1
  fi
  if grep -q "'type': 'error'" "$out"; then
    log "ERROR: an error frame was observed:"
    cat "$out"
    rm -f "$out"
    return 1
  fi

  # Concatenate token content (not the raw frames) so the check is robust to
  # the model wrapping the filename in extra prose - same technique as
  # `persistence_smoke.sh`'s name check.
  local reply
  reply="$(python3 -c "
import ast, sys

tokens = []
for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    try:
        frame = ast.literal_eval(line)
    except (ValueError, SyntaxError):
        continue
    if isinstance(frame, dict) and frame.get('type') == 'token':
        tokens.append(frame.get('content', ''))
print(''.join(tokens))
" <"$out")"
  rm -f "$out"

  log "Reply content: ${reply}"
  printf '%s' "$reply" | grep -qi "$FOLLOWUP_MENTION"
}

step_continue_conversation() {
  log "Step 6/7: continuing thread ${THREAD_ID} - \"what file did you just create?\"..."
  if ask_what_file_and_check_mention; then
    log "OK: reply mentions '${FOLLOWUP_MENTION}' on attempt 1"
    return 0
  fi

  log "WARN: reply did not mention '${FOLLOWUP_MENTION}' on attempt 1 - retrying once (LLM nondeterminism allowance)"
  if ask_what_file_and_check_mention; then
    log "OK: reply mentions '${FOLLOWUP_MENTION}' on attempt 2"
    return 0
  fi

  log "ERROR: reply never mentioned '${FOLLOWUP_MENTION}' after 2 attempts - gate FAILS"
  return 1
}

cleanup() {
  # Always runs (success or failure) so the script is safely re-runnable
  # (Tier A requires two green runs in a row) - mirrors `gate_m2.sh`'s and
  # `files_rest_smoke.sh`'s own EXIT-trap convention.
  if [ -n "$SAVED_HITL" ]; then
    bash "${SCRIPT_DIR}/ensure_hitl.sh" "$SAVED_HITL" >/dev/null 2>&1 || true
  fi
  e2e_personal_rm "$FILE_PATH"
  if [ -n "${E2E_AUTH_COOKIE:-}" ] && [ -z "$(e2e_personal_ls "$FILE_DIR" 2>/dev/null)" ]; then
    e2e_personal_rm "$FILE_DIR"
  fi
  if [ -n "${THREAD_ID:-}" ]; then
    rest_request DELETE "${API_BASE}/threads/${THREAD_ID}" >/dev/null 2>&1 || true
    # Best-effort: the LLM may have used execute_code (not just file tools)
    # to satisfy the write prompt, which would have created a code-exec-manager
    # session/container keyed by THREAD_ID - see M6-03 note above.
    docker exec homeai-code-exec-manager-1 python3 -c "
import sys, urllib.request
try:
    urllib.request.urlopen(urllib.request.Request(f'http://localhost:8090/sessions/{sys.argv[1]}', method='DELETE'), timeout=15)
except Exception:
    pass
" "$THREAD_ID" >/dev/null 2>&1 || true
  fi
  e2e_auth_end
}
trap cleanup EXIT

main() {
  log "=== GATE M3 (G3): restart persistence + files round-trip ==="
  step_stack_up_and_healthy
  # The session lives in the platform DB, so it survives the full restart.
  e2e_auth_begin gate-m3
  # M8-03 made HITL on by default; this gate's mutating tools are not
  # wired to send approval_response, so turn HITL off for the run.
  log "Turning hitl_enabled off so write_file/execute_code are not interrupted..."
  SAVED_HITL="$(bash "${SCRIPT_DIR}/ensure_hitl.sh" false)"
  log "OK: hitl_enabled=false (was ${SAVED_HITL})"
  step_create_thread_and_write_file
  step_files_api_round_trip
  step_full_restart
  step_thread_and_file_survived
  step_continue_conversation
  log "Step 7/7: cleanup (via EXIT trap)."
  echo "GATE M3: PASS"
}

main

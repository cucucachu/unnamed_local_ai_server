#!/usr/bin/env bash
# M3-02 acceptance: manual curl pass (Conventions & Contracts §5 "Threads")
# against the live stack, scripted for repeatability — the "Manual curl pass"
# Tier A acceptance criterion, automated so it's re-runnable rather than a
# one-off human check.
#
# From a running (or freshly brought-up) compose stack, this script:
#   1. Creates a thread via `POST /api/threads` (default title "New chat").
#   2. Confirms it appears in `GET /api/threads`.
#   3. Runs a real WS turn on it via `scripts/ws_smoke.py` with a >60-char
#      prompt.
#   4. Confirms the thread's title changed (first 60 chars of the prompt,
#      single-line, `...`-truncated — same normalization `chat_ws.py`'s
#      `_derive_title` applies, recomputed here in Python so this script
#      doesn't hardcode a magic truncated string) and `updated_at` bumped
#      past `created_at`.
#   5. Confirms `GET .../messages` shows the turn, correctly normalized
#      (user message content matches the full untruncated prompt; an
#      assistant reply follows).
#   6. Deletes the thread (`DELETE /api/threads/{id}` -> 204).
#   7. Confirms it's gone from the list and `GET .../messages` now 404s.
#
# `curl` is NOT installed on this host — this script uses Python's
# `urllib.request` for the REST calls (JSON in/out, needs real parsing
# anyway) rather than `wget`, mirroring `gate_m2.sh`/`persistence_smoke.sh`'s
# own precedent of embedding Python for anything beyond a bare reachability
# check. `scripts/ws_smoke.py` (already parameterized via
# `WS_SMOKE_THREAD_ID`/`WS_SMOKE_PROMPT`, M2-07) is reused as-is for the WS
# turn, exactly like the other two `scripts/e2e/*.sh` gates.
#
# Usage:
#   scripts/e2e/threads_rest_smoke.sh
#
# Exits non-zero (and prints the failing step) if any check fails.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$REPO_ROOT"

API_BASE="http://localhost/api"
LONG_PROMPT="Please reply with one short friendly sentence for this end-to-end smoke test of the threads REST API."

API_HEALTH_TIMEOUT_S=120
WS_TURN_TIMEOUT_S=90

log() {
  echo "[threads-rest-smoke] $(date '+%H:%M:%S') $*"
}

# ---- curl-equivalent / polling helpers (curl not installed; wget is) -------

http_ok() {
  # 0 if a GET to $1 is reachable with a successful (2xx/3xx) status.
  #
  # NOTE: deliberately NOT `wget --spider` - this wget version (1.25.0)
  # sends a HEAD request in spider mode, and /api/health is a GET-only
  # FastAPI route (405s on HEAD) - confirmed in `gate_m2.sh`. `-O /dev/null`
  # forces a real GET while still discarding the body.
  wget -q -O /dev/null --timeout=10 --tries=1 "$1" >/dev/null 2>&1
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

# ---- REST helpers (Python's urllib - JSON in/out, `curl` unavailable) ------

# Prints the HTTP status code on line 1, the raw response body (compact
# single-line JSON, or empty for 204) on line 2.
rest_request() {
  local method="$1" url="$2" json_body="${3:-}"
  python3 - "$method" "$url" "$json_body" <<'PY'
import sys
import urllib.error
import urllib.request

method, url, json_body = sys.argv[1], sys.argv[2], sys.argv[3]
data = json_body.encode() if json_body else None
headers = {"Content-Type": "application/json"} if data else {}
req = urllib.request.Request(url, data=data, method=method, headers=headers)
try:
    with urllib.request.urlopen(req, timeout=10) as resp:
        print(resp.status)
        print(resp.read().decode())
except urllib.error.HTTPError as e:
    print(e.code)
    print(e.read().decode())
PY
}

# $1: response body (a JSON object). $2: field name. Prints the field value
# (as a string - fine for the id/title/created_at/updated_at strings this
# script reads).
json_field() {
  python3 -c "
import json, sys
print(json.loads(sys.argv[1])[sys.argv[2]])
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

# ---- gate steps -------------------------------------------------------------

step_stack_up_and_healthy() {
  log "Step 1/7: bringing up the compose stack (idempotent if already up)..."
  docker compose up -d --build agent-server
  log "Waiting for agent-server API health (timeout ${API_HEALTH_TIMEOUT_S}s)..."
  if ! wait_for_api_health "$API_HEALTH_TIMEOUT_S"; then
    log "ERROR: ${API_BASE}/health never came up within ${API_HEALTH_TIMEOUT_S}s"
    return 1
  fi
  log "OK: ${API_BASE}/health OK"
}

step_create_thread() {
  log "Step 2/7: POST /api/threads (no title -> default \"New chat\")..."
  local resp status body
  resp="$(rest_request POST "${API_BASE}/threads" '{}')"
  status="$(sed -n '1p' <<<"$resp")"
  body="$(sed -n '2p' <<<"$resp")"

  if [ "$status" != "201" ]; then
    log "ERROR: expected 201, got ${status}: ${body}"
    return 1
  fi

  THREAD_ID="$(json_field "$body" id)"
  THREAD_TITLE="$(json_field "$body" title)"
  THREAD_CREATED_AT="$(json_field "$body" created_at)"

  if [ "$THREAD_TITLE" != "New chat" ]; then
    log "ERROR: expected default title \"New chat\", got \"${THREAD_TITLE}\""
    return 1
  fi
  log "OK: created thread ${THREAD_ID} (title=\"${THREAD_TITLE}\")"
}

step_appears_in_list() {
  log "Step 3/7: GET /api/threads - confirm ${THREAD_ID} is present..."
  local resp status body present
  resp="$(rest_request GET "${API_BASE}/threads")"
  status="$(sed -n '1p' <<<"$resp")"
  body="$(sed -n '2p' <<<"$resp")"

  if [ "$status" != "200" ]; then
    log "ERROR: expected 200, got ${status}: ${body}"
    return 1
  fi
  present="$(json_array_contains_id "$body" "$THREAD_ID")"
  if [ "$present" != "true" ]; then
    log "ERROR: thread ${THREAD_ID} not found in list: ${body}"
    return 1
  fi
  log "OK: thread ${THREAD_ID} present in the list"
}

step_run_ws_turn() {
  log "Step 4/7: running a real WS turn on thread ${THREAD_ID}..."
  local out
  out="$(mktemp)"
  if ! WS_SMOKE_THREAD_ID="$THREAD_ID" WS_SMOKE_PROMPT="$LONG_PROMPT" \
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
  if ! grep -q "'type': 'turn_end'" "$out"; then
    log "ERROR: no turn_end frame observed:"
    cat "$out"
    rm -f "$out"
    return 1
  fi
  log "OK: WS turn completed with no error frame"
  rm -f "$out"
}

step_title_changed_and_bumped() {
  log "Step 5/7: title changed + updated_at bumped..."
  local resp status body updated_title updated_at
  resp="$(rest_request GET "${API_BASE}/threads")"
  status="$(sed -n '1p' <<<"$resp")"
  body="$(sed -n '2p' <<<"$resp")"
  if [ "$status" != "200" ]; then
    log "ERROR: expected 200, got ${status}: ${body}"
    return 1
  fi

  updated_title="$(python3 -c "
import json, sys
threads, thread_id = json.loads(sys.argv[1]), sys.argv[2]
match = next(t for t in threads if t['id'] == thread_id)
print(match['title'])
" "$body" "$THREAD_ID")"
  updated_at="$(python3 -c "
import json, sys
threads, thread_id = json.loads(sys.argv[1]), sys.argv[2]
match = next(t for t in threads if t['id'] == thread_id)
print(match['updated_at'])
" "$body" "$THREAD_ID")"

  # Recompute the expected title with the SAME normalization
  # `chat_ws.py::_derive_title` applies, rather than hardcoding a magic
  # truncated string here.
  local expected_title
  expected_title="$(python3 -c "
import sys
content = sys.argv[1]
single_line = ' '.join(content.split())
print(single_line[:60] + '...' if len(single_line) > 60 else single_line)
" "$LONG_PROMPT")"

  if [ "$updated_title" != "$expected_title" ]; then
    log "ERROR: expected title \"${expected_title}\", got \"${updated_title}\""
    return 1
  fi

  local bumped
  bumped="$(python3 -c "
from datetime import datetime
import sys
created, updated = sys.argv[1], sys.argv[2]
print('true' if datetime.fromisoformat(updated) > datetime.fromisoformat(created) else 'false')
" "$THREAD_CREATED_AT" "$updated_at")"
  if [ "$bumped" != "true" ]; then
    log "ERROR: updated_at (${updated_at}) did not advance past created_at (${THREAD_CREATED_AT})"
    return 1
  fi
  log "OK: title -> \"${updated_title}\", updated_at bumped past created_at"
}

step_messages_show_turn() {
  log "Step 6/7: GET /api/threads/{id}/messages shows the turn..."
  local resp status body
  resp="$(rest_request GET "${API_BASE}/threads/${THREAD_ID}/messages")"
  status="$(sed -n '1p' <<<"$resp")"
  body="$(sed -n '2p' <<<"$resp")"
  if [ "$status" != "200" ]; then
    log "ERROR: expected 200, got ${status}: ${body}"
    return 1
  fi

  python3 -c "
import json, sys
messages, prompt = json.loads(sys.argv[1]), sys.argv[2]
assert len(messages) >= 2, f'expected >=2 messages (user+assistant), got {messages!r}'
assert messages[0]['role'] == 'user', messages[0]
assert messages[0]['content'] == prompt, (messages[0]['content'], prompt)
assert any(m['role'] == 'assistant' and m['content'] for m in messages[1:]), messages
" "$body" "$LONG_PROMPT"
  log "OK: messages normalized correctly (user prompt + assistant reply)"
}

step_delete_and_confirm_gone() {
  log "Step 7/7: DELETE thread, confirm gone from list + messages 404s..."
  local resp status body

  resp="$(rest_request DELETE "${API_BASE}/threads/${THREAD_ID}")"
  status="$(sed -n '1p' <<<"$resp")"
  if [ "$status" != "204" ]; then
    log "ERROR: expected 204 from DELETE, got ${status}"
    return 1
  fi

  resp="$(rest_request GET "${API_BASE}/threads")"
  status="$(sed -n '1p' <<<"$resp")"
  body="$(sed -n '2p' <<<"$resp")"
  if [ "$(json_array_contains_id "$body" "$THREAD_ID")" != "false" ]; then
    log "ERROR: thread ${THREAD_ID} still present in list after DELETE: ${body}"
    return 1
  fi

  resp="$(rest_request GET "${API_BASE}/threads/${THREAD_ID}/messages")"
  status="$(sed -n '1p' <<<"$resp")"
  if [ "$status" != "404" ]; then
    log "ERROR: expected 404 from GET messages after DELETE, got ${status}"
    return 1
  fi

  log "OK: thread ${THREAD_ID} gone from list, messages now 404"
}

main() {
  log "=== THREADS REST SMOKE (M3-02): create -> list -> WS turn -> title/bump -> messages -> delete ==="
  step_stack_up_and_healthy
  step_create_thread
  step_appears_in_list
  step_run_ws_turn
  step_title_changed_and_bumped
  step_messages_show_turn
  step_delete_and_confirm_gone
  echo "THREADS REST SMOKE: PASS"
}

main

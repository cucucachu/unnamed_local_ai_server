#!/usr/bin/env bash
# M10-04 acceptance: auth enforcement and per-user thread isolation through
# Caddy, against the live stack, with two throwaway `e2e-*` users (Alice and
# Bob, via `lib/auth.sh`).
#
#   1. Unauthenticated: `GET /api/threads` -> 401, and a `/ws/chat/...`
#      upgrade is refused with HTTP 401 before it reaches agent-server.
#   2. Alice creates a thread and runs a real WS turn on it.
#   3. A client-supplied `X-HomeAI-Identity` is never trusted: without a
#      session even Alice's genuine, unexpired identity token -> 401; with
#      Bob's session plus Alice's token, the request is still Bob's (`/me`
#      is Bob, Alice's thread 404s) because Caddy replaces the header.
#   4. Bob gets Alice's thread as nonexistent: messages/branches/
#      active_branch -> 404, `/state` -> no pending approval, not in his
#      list, `DELETE` -> 204 no-op, and the chat socket closes with 4404
#      before sending any frame.
#   5. Alice's thread and messages are untouched.
#
# The identity token in step 3 is minted by calling the platform's
# `/internal/auth/verify` from inside the platform container (the same call
# Caddy makes) with Alice's session; it is never printed.
#
# Usage:
#   scripts/e2e/tenancy_threads_smoke.sh
#
# Exits non-zero (and prints the failing step) if any check fails.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$REPO_ROOT"
# shellcheck source=lib/auth.sh
source "$SCRIPT_DIR/lib/auth.sh"
trap e2e_auth_end EXIT

API_BASE="${E2E_BASE}/api"
WS_BASE="${E2E_BASE/http/ws}/ws"
API_HEALTH_TIMEOUT_S=120
WS_TURN_TIMEOUT_S=90

log() {
  echo "[tenancy-threads-smoke] $(date '+%H:%M:%S') $*"
}

fail() {
  log "ERROR: $*"
  exit 1
}

wait_for_api_health() {
  local deadline=$(( $(date +%s) + API_HEALTH_TIMEOUT_S ))
  while (( $(date +%s) < deadline )); do
    wget -q -O /dev/null --timeout=10 --tries=1 "${API_BASE}/health" >/dev/null 2>&1 && return 0
    sleep 3
  done
  return 1
}

# $1 method, $2 path under /api, $3 cookie ('' for none), $4 identity header
# value ('' for none), $5 JSON body. Prints the status on line 1 and the
# body on line 2.
req() {
  python3 - "$API_BASE" "$@" <<'PY'
import sys
import urllib.error
import urllib.request

base, method, path, cookie, identity = sys.argv[1:6]
body = sys.argv[6] if len(sys.argv) > 6 else ""
headers = {}
if cookie:
    headers["Cookie"] = cookie
if identity:
    headers["X-HomeAI-Identity"] = identity
if body:
    headers["Content-Type"] = "application/json"
r = urllib.request.Request(base + path, data=body.encode() or None, method=method, headers=headers)
try:
    with urllib.request.urlopen(r, timeout=15) as resp:
        print(resp.status)
        print(resp.read().decode().replace("\n", " "))
except urllib.error.HTTPError as e:
    print(e.code)
    print(e.read().decode().replace("\n", " "))
PY
}

# $1 expected status, $2 label, rest: req args. Sets BODY.
expect() {
  local want="$1" label="$2" out status
  shift 2
  out="$(req "$@")"
  status="$(sed -n 1p <<<"$out")"
  BODY="$(sed -n 2p <<<"$out")"
  [ "$status" = "$want" ] || fail "${label}: expected ${want}, got ${status}: ${BODY}"
  log "OK: ${label} -> ${status}"
}

# $1 thread id ('' = a random one), $2 cookie ('' for none). Prints
# `http <status>`, `closed <code>` (no frames before the close), or `open`.
ws_probe() {
  uvx --from websockets python - "$WS_BASE" "$1" "$2" <<'PY'
import asyncio
import sys
import uuid

from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed, InvalidStatus

base, thread_id, cookie = sys.argv[1], sys.argv[2] or str(uuid.uuid4()), sys.argv[3]


async def main() -> None:
    headers = {"Cookie": cookie} if cookie else {}
    try:
        async with connect(f"{base}/chat/{thread_id}", additional_headers=headers) as ws:
            try:
                frame = await asyncio.wait_for(ws.recv(), timeout=5)
                print(f"frame {frame[:120]}")
            except ConnectionClosed as e:
                print(f"closed {e.rcvd.code if e.rcvd else 'none'}")
            except TimeoutError:
                print("open")
    except InvalidStatus as e:
        print(f"http {e.response.status_code}")


asyncio.run(main())
PY
}

# Alice's identity token, straight from the platform (see header).
mint_identity() {
  docker compose exec -T platform python -c '
import sys, urllib.request
req = urllib.request.Request("http://localhost:8100/internal/auth/verify", headers={"Cookie": sys.stdin.read().strip()})
with urllib.request.urlopen(req, timeout=10) as resp:
    print(resp.headers["X-HomeAI-Identity"])
' <<<"$1"
}

step_unauthenticated() {
  log "Step 1/5: unauthenticated requests are refused at Caddy..."
  expect 401 "GET /api/threads without a session" GET /threads "" ""
  local ws
  ws="$(ws_probe "" "")"
  [ "$ws" = "http 401" ] || fail "WS upgrade without a session: expected 'http 401', got '${ws}'"
  log "OK: WS upgrade without a session -> ${ws}"
}

step_alice_thread() {
  log "Step 2/5: Alice creates a thread and runs a WS turn on it..."
  expect 201 "Alice POST /api/threads" POST /threads "$ALICE" "" '{}'
  THREAD_ID="$(python3 -c 'import json, sys; print(json.loads(sys.argv[1])["id"])' "$BODY")"
  E2E_AUTH_COOKIE="$ALICE" WS_SMOKE_THREAD_ID="$THREAD_ID" WS_SMOKE_PROMPT="Say exactly: PONG" \
    timeout "$WS_TURN_TIMEOUT_S" uvx --from websockets python "$REPO_ROOT/scripts/ws_smoke.py" >/tmp/tenancy-ws.log 2>&1 \
    || { cat /tmp/tenancy-ws.log; fail "Alice's WS turn failed"; }
  grep -q "'type': 'turn_end'" /tmp/tenancy-ws.log || { cat /tmp/tenancy-ws.log; fail "Alice's WS turn never ended"; }
  expect 200 "Alice GET messages" GET "/threads/${THREAD_ID}/messages" "$ALICE" ""
  [ "$BODY" != "[]" ] || fail "Alice's thread has no messages after her turn"
}

step_forged_identity() {
  log "Step 3/5: client-supplied X-HomeAI-Identity is ignored..."
  local token
  token="$(mint_identity "$ALICE")"
  [ -n "$token" ] || fail "could not mint Alice's identity token"
  expect 401 "Alice's genuine token, no session: GET /api/threads" GET /threads "" "$token"
  expect 401 "Alice's genuine token, no session: GET /api/platform/me" GET /platform/me "" "$token"
  expect 401 "forged garbage token, no session" GET /threads "" "forged.not.ajwt"
  expect 200 "Bob's session + Alice's token: GET /api/platform/me" GET /platform/me "$BOB" "$token"
  python3 -c 'import json, sys; sys.exit(json.loads(sys.argv[1])["username"] != sys.argv[2])' "$BODY" "$BOB_USER" \
    || fail "/api/platform/me answered as someone other than Bob: ${BODY}"
  log "OK: /api/platform/me is still Bob"
  expect 404 "Bob's session + Alice's token: GET Alice's messages" GET "/threads/${THREAD_ID}/messages" "$BOB" "$token"
}

step_bob_isolated() {
  log "Step 4/5: Bob sees Alice's thread as nonexistent..."
  expect 404 "Bob GET messages" GET "/threads/${THREAD_ID}/messages" "$BOB" ""
  expect 404 "Bob GET branches" GET "/threads/${THREAD_ID}/branches" "$BOB" ""
  expect 404 "Bob PUT active_branch" PUT "/threads/${THREAD_ID}/active_branch" "$BOB" "" '{"checkpoint_id": "x"}'
  expect 200 "Bob GET state" GET "/threads/${THREAD_ID}/state" "$BOB" ""
  [ "$BODY" = '{"pending_approval":null}' ] || fail "Bob GET state leaked something: ${BODY}"
  expect 200 "Bob GET /api/threads" GET /threads "$BOB" ""
  if grep -q "$THREAD_ID" <<<"$BODY"; then fail "Alice's thread is in Bob's list: ${BODY}"; fi
  log "OK: Alice's thread absent from Bob's list"
  expect 204 "Bob DELETE (idempotent no-op)" DELETE "/threads/${THREAD_ID}" "$BOB" ""
  local ws
  ws="$(ws_probe "$THREAD_ID" "$BOB")"
  [ "$ws" = "closed 4404" ] || fail "Bob's WS on Alice's thread: expected 'closed 4404', got '${ws}'"
  log "OK: Bob's WS on Alice's thread -> ${ws}"
  ws="$(ws_probe "" "$BOB")"
  [ "$ws" = "closed 4404" ] || fail "Bob's WS on an unknown thread: expected 'closed 4404', got '${ws}'"
  log "OK: Bob's WS on an unknown thread -> ${ws} (same as a foreign one)"
}

step_alice_intact() {
  log "Step 5/5: Alice's thread is untouched..."
  expect 200 "Alice GET /api/threads" GET /threads "$ALICE" ""
  grep -q "$THREAD_ID" <<<"$BODY" || fail "Alice's thread vanished from her list: ${BODY}"
  expect 200 "Alice GET messages" GET "/threads/${THREAD_ID}/messages" "$ALICE" ""
  [ "$BODY" != "[]" ] || fail "Alice's messages vanished"
}

main() {
  log "=== TENANCY THREADS SMOKE (M10-04): auth at Caddy, forged identity ignored, per-user threads ==="
  wait_for_api_health || fail "${API_BASE}/health never came up within ${API_HEALTH_TIMEOUT_S}s"
  e2e_auth_create_user alice
  ALICE="$(e2e_auth_login "$E2E_NEW_USER" "$E2E_NEW_PASSWORD")"
  e2e_auth_create_user bob
  BOB_USER="$E2E_NEW_USER"
  BOB="$(e2e_auth_login "$E2E_NEW_USER" "$E2E_NEW_PASSWORD")"
  step_unauthenticated
  step_alice_thread
  step_forged_identity
  step_bob_isolated
  step_alice_intact
  echo "TENANCY THREADS SMOKE: PASS"
}

main

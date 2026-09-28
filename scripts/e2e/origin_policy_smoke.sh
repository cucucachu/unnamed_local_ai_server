#!/usr/bin/env bash
# M15-02: live origin-policy check through Caddy (not platform:8100).
#
# A real LAN request that *also* sends `X-Forwarded-For: 8.8.8.8` must
# not become public — Caddy overwrites client-supplied XFF (no
# trusted_proxies). POST /api/auth/setup should be 409 setup_complete or
# 401 invalid_setup_code, never 403 public_origin.
#
# Optionally creates a throwaway WireGuard peer as an e2e-* human with
# the same spoofed XFF (must not 403 public_origin) and revokes it.
#
# Needs the live stack (caddy + platform). Never completes bootstrap.
# Never recreates model-runner or postgres. Never rebuilds caddy.
# Takes `/tmp/homeai-stack.lock` if the caller hasn't.
# Fail clearly if Caddy is down (do not skip).
#
# Usage: scripts/e2e/origin_policy_smoke.sh

set -euo pipefail

if [ -z "${HOMEAI_STACK_LOCK:-}" ]; then
  exec flock /tmp/homeai-stack.lock env HOMEAI_STACK_LOCK=1 "$0" "$@"
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$REPO_ROOT"

export COMPOSE_PROJECT_NAME=homeai

# Worktrees don't get the gitignored .env; compose still needs it. Never cat it.
if [ ! -f .env ] && [ -f /home/cody/code/unnamed_local_ai_server/.env ]; then
  ln -s /home/cody/code/unnamed_local_ai_server/.env .env
fi

# shellcheck source=lib/auth.sh
source "$SCRIPT_DIR/lib/auth.sh"

E2E_BASE="${E2E_BASE:-http://localhost}"
PEER_ID=""

log() { echo "[origin-policy-smoke] $(date '+%H:%M:%S') $*"; }

fail() {
  echo "FAIL: $*" >&2
  exit 1
}

cid_of() {
  docker compose -p homeai ps -q "$1" 2>/dev/null | head -n1
}

cleanup() {
  if [ -n "$PEER_ID" ]; then
    python3 - "$E2E_BASE" "${E2E_AUTH_COOKIE:-}" "$PEER_ID" <<'PY' >/dev/null 2>&1 || true
import sys, urllib.request
base, cookie, peer = sys.argv[1:4]
req = urllib.request.Request(
    f"{base}/api/platform/me/wireguard-devices/{peer}",
    method="DELETE",
    headers={"Cookie": cookie} if cookie else {},
)
try:
    urllib.request.urlopen(req, timeout=10)
except Exception:
    pass
PY
  fi
  e2e_auth_end
}
trap cleanup EXIT

PG_BEFORE="$(cid_of postgres)"
MR_BEFORE="$(cid_of model-runner)"
log "postgres id=${PG_BEFORE:-?} model-runner id=${MR_BEFORE:-?} (must stay unchanged)"

log "building platform only (no-deps; not caddy/postgres/model-runner)"
docker compose -p homeai up -d --build --no-deps platform

PG_AFTER="$(cid_of postgres)"
MR_AFTER="$(cid_of model-runner)"
[ "$PG_BEFORE" = "$PG_AFTER" ] || fail "postgres container id changed ($PG_BEFORE -> $PG_AFTER)"
[ "$MR_BEFORE" = "$MR_AFTER" ] || fail "model-runner container id changed ($MR_BEFORE -> $MR_AFTER)"
log "postgres and model-runner ids unchanged"

log "waiting for platform health"
for _ in $(seq 1 30); do
  if docker compose -p homeai exec -T platform python -c \
    "import urllib.request; urllib.request.urlopen('http://localhost:8100/internal/health', timeout=3)" \
    >/dev/null 2>&1; then
    break
  fi
  sleep 2
done
docker compose -p homeai exec -T platform python -c \
  "import urllib.request; urllib.request.urlopen('http://localhost:8100/internal/health', timeout=3)" \
  >/dev/null || fail "platform not healthy"

log "checking Caddy on ${E2E_BASE} (must not skip if down)"
CADDY_OK=0
for _ in $(seq 1 15); do
  if python3 - "$E2E_BASE" <<'PY' >/dev/null 2>&1; then
import sys, urllib.error, urllib.request
try:
    urllib.request.urlopen(sys.argv[1] + "/", timeout=5)
except urllib.error.HTTPError:
    pass
PY
    CADDY_OK=1
    break
  fi
  sleep 1
done
[ "$CADDY_OK" -eq 1 ] || fail "Caddy is not up at ${E2E_BASE} (open http://localhost/; do not skip)"

log "POST /api/auth/setup through Caddy with spoofed X-Forwarded-For: 8.8.8.8"
SETUP_OUT="$(python3 - "$E2E_BASE" <<'PY'
import json, sys, urllib.error, urllib.request

base = sys.argv[1]
body = json.dumps({
    "setup_code": "AAAA-AAAA-AAAA-AAAA",
    "username": "must-not-create",
    "display_name": "Must Not",
    "password": "correct horse battery",
}).encode()
req = urllib.request.Request(
    f"{base}/api/auth/setup",
    data=body,
    method="POST",
    headers={
        "Content-Type": "application/json",
        "X-Forwarded-For": "8.8.8.8",
        "X-HomeAI-Client": "native",
    },
)
try:
    with urllib.request.urlopen(req, timeout=15) as resp:
        print(resp.status)
        print(resp.read().decode())
except urllib.error.HTTPError as e:
    print(e.code)
    print(e.read().decode())
PY
)"
SETUP_CODE="$(head -n1 <<<"$SETUP_OUT")"
SETUP_BODY="$(tail -n +2 <<<"$SETUP_OUT")"
log "setup -> HTTP ${SETUP_CODE} ${SETUP_BODY}"
echo "$SETUP_BODY" | grep -q 'public_origin' && \
  fail "Caddy did not overwrite spoofed X-Forwarded-For (got public_origin)"
case "$SETUP_CODE" in
  401|409) log "ok   setup spoof is ${SETUP_CODE} (not public_origin)" ;;
  *) fail "setup spoof: expected 401 invalid_setup_code or 409 setup_complete, got HTTP ${SETUP_CODE} ${SETUP_BODY}" ;;
esac
echo "$SETUP_BODY" | grep -Eq 'invalid_setup_code|setup_complete' || \
  fail "setup spoof: body should be invalid_setup_code or setup_complete, got ${SETUP_BODY}"

log "creating throwaway human; POST wireguard-devices with spoofed XFF"
e2e_auth_begin origin
PEER_META="$(python3 - "$E2E_BASE" "$E2E_AUTH_COOKIE" <<'PY'
import json, sys, urllib.error, urllib.request

base, cookie = sys.argv[1:3]
body = json.dumps({"name": "e2e-origin-spoof"}).encode()
req = urllib.request.Request(
    f"{base}/api/platform/me/wireguard-devices",
    data=body,
    method="POST",
    headers={
        "Content-Type": "application/json",
        "Cookie": cookie,
        "X-Forwarded-For": "8.8.8.8",
    },
)
try:
    with urllib.request.urlopen(req, timeout=15) as resp:
        data = json.loads(resp.read().decode())
except urllib.error.HTTPError as e:
    body = e.read().decode()
    raise SystemExit(f"create peer: HTTP {e.code} {body}") from e
print(data["id"])
PY
)"
[[ "$PEER_META" =~ ^[0-9a-f-]{36}$ ]] || fail "create peer: bad id in '$PEER_META'"
PEER_ID="$PEER_META"
log "ok   POST wireguard-devices with spoofed XFF created id=${PEER_ID} (not public_origin)"

python3 - "$E2E_BASE" "$E2E_AUTH_COOKIE" "$PEER_ID" <<'PY'
import sys, urllib.error, urllib.request
base, cookie, peer = sys.argv[1:4]
req = urllib.request.Request(
    f"{base}/api/platform/me/wireguard-devices/{peer}",
    method="DELETE",
    headers={"Cookie": cookie},
)
try:
    urllib.request.urlopen(req, timeout=15)
except urllib.error.HTTPError as e:
    if e.code != 204:
        raise SystemExit(f"revoke: HTTP {e.code} {e.read().decode()}") from e
print("ok   revoked peer")
PY
PEER_ID=""

log "PASS"

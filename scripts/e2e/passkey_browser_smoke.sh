#!/usr/bin/env bash
# M15-04 passkey browser smoke: Playwright + Chrome CDP virtual authenticator
# through Caddy at http://localhost (a WebAuthn secure context).
#
# Under flock: rebuild platform and caddy from this tree (--no-deps; never
# postgres/model-runner), bring platform up with WEBAUTHN_RP_ID=localhost
# (never HOMEAI_DOMAIN — that would start ACME), run the flow, then restore
# platform without WEBAUTHN_RP_ID. Throwaway e2e-* user; never completes
# bootstrap. See passkey_browser_smoke.mjs.
#
# Usage: scripts/e2e/passkey_browser_smoke.sh

set -euo pipefail

if [ -z "${HOMEAI_STACK_LOCK:-}" ]; then
  exec flock /tmp/homeai-stack.lock env HOMEAI_STACK_LOCK=1 "$0" "$@"
fi

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$script_dir/../.." && pwd)"
cd "$REPO_ROOT"

export COMPOSE_PROJECT_NAME=homeai

# Worktrees don't get the gitignored .env; compose still needs it. Never cat it.
if [ ! -f .env ] && [ -f /home/cody/code/unnamed_local_ai_server/.env ]; then
  ln -s /home/cody/code/unnamed_local_ai_server/.env .env
fi

cid_of() {
  docker compose -p homeai ps -q "$1" 2>/dev/null | head -n1
}

log() { echo "[passkey-smoke] $(date '+%H:%M:%S') $*"; }

fail() {
  echo "FAIL: $*" >&2
  exit 1
}

restore_platform() {
  log "restoring platform without WEBAUTHN_RP_ID"
  docker compose -p homeai up -d --no-deps --no-build platform
  rp="$(docker compose -p homeai exec -T platform python -c \
    "from app.core.config import Settings; s=Settings(); print('set' if (s.webauthn_rp_id or '').strip() else 'empty')")"
  domain="$(docker compose -p homeai exec -T platform python -c \
    "from app.core.config import Settings; s=Settings(); print('set' if (s.homeai_domain or '').strip() else 'empty')")"
  log "restored WEBAUTHN_RP_ID=$rp HOMEAI_DOMAIN=$domain"
  [ "$rp" = "empty" ] || echo "WARN: WEBAUTHN_RP_ID was not empty after restore" >&2
  [ "$domain" = "empty" ] || echo "WARN: HOMEAI_DOMAIN is set after restore" >&2
}

PG_BEFORE="$(cid_of postgres)"
MR_BEFORE="$(cid_of model-runner)"
log "postgres id=${PG_BEFORE:-?} model-runner id=${MR_BEFORE:-?} (must stay unchanged)"

log "rebuilding platform and caddy from this tree (--no-deps; not postgres/model-runner)"
docker compose -p homeai up -d --build --no-deps caddy
# WEBAUTHN_RP_ID applies only to this invocation (not exported).
WEBAUTHN_RP_ID=localhost docker compose -p homeai up -d --build --no-deps platform

PG_AFTER="$(cid_of postgres)"
MR_AFTER="$(cid_of model-runner)"
[ "$PG_BEFORE" = "$PG_AFTER" ] || fail "postgres container id changed ($PG_BEFORE -> $PG_AFTER)"
[ "$MR_BEFORE" = "$MR_AFTER" ] || fail "model-runner container id changed ($MR_BEFORE -> $MR_AFTER)"
log "postgres and model-runner ids unchanged"

trap restore_platform EXIT

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

rp="$(docker compose -p homeai exec -T platform python -c \
  "from app.core.config import Settings; s=Settings(); print(s.webauthn_rp_id or '')")"
[ "$rp" = "localhost" ] || fail "expected WEBAUTHN_RP_ID=localhost on platform, got '$rp'"
domain="$(docker compose -p homeai exec -T platform python -c \
  "from app.core.config import Settings; s=Settings(); print(s.homeai_domain or '')")"
[ -z "$domain" ] || fail "HOMEAI_DOMAIN must stay unset for this smoke (got a value)"

if [ ! -d "$script_dir/node_modules/playwright" ]; then
  echo "==> Installing Playwright (npm install)..."
  (cd "$script_dir" && npm install)
fi
echo "==> Ensuring the Chromium browser binary is installed..."
(cd "$script_dir" && npx playwright install chromium)

echo "==> Running the passkey smoke against ${PASSKEY_SMOKE_BASE_URL:-http://localhost/}..."
if ! node "$script_dir/passkey_browser_smoke.mjs"; then
  log "platform logs (tail) for the failed smoke:"
  docker compose -p homeai logs --tail=80 platform >&2 || true
  exit 1
fi

log "PASS"

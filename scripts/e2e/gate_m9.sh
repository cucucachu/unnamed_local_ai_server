#!/usr/bin/env bash
# M9-07 GATE G9: chat experience from a phone (markdown, activity panel,
# file links, keyboard, voice).
#
# Milestone gate for M9. Proves the whole chat-experience surface works
# together over the real LAN posture: HTTPS via Caddy's internal CA
# (M9-05), with the Playwright coverage for markdown rendering (M9-01),
# the turn activity panel (M9-02), file: deep links (M9-03), and the
# voice-input mic (M9-06) all already living inside
# `chat_browser_smoke.mjs` (that script also covers M9-04's Android
# keyboard fix at the component level, and is chained here rather than
# duplicated — same "chain, don't duplicate" convention `gate_m7.sh` and
# `gate_m8.sh` already use for their own dependencies).
#
# From a running (or freshly brought-up) compose stack, this script runs,
# in this exact order, failing fast (clear message, non-zero exit) on the
# first failure:
#   1. Stack healthy — `docker compose up -d --build`, wait for
#      model-runner healthy + agent-server `/api/health` (same helpers as
#      every other `scripts/e2e/gate_*.sh`).
#   2. `scripts/verify_network.sh` (needs sudo) — LAN-only posture,
#      including M9-05's `https://homeai.local` still being reachable
#      alongside `http://homeai.local` (checks 2 and 8 hit the plain-HTTP
#      path; this gate's own step 4 below is what proves HTTPS end to
#      end). Same "needs sudo, not re-exec'd automatically" reasoning as
#      `gate_m7.sh`'s own step 1 — see that script's header comment,
#      verbatim.
#   3. `scripts/export-ca.sh` — forces Caddy to issue/export its local-CA
#      root cert to `${BACKUP_DIR}/homeai-root-ca.crt` so step 4 has a CA
#      to trust (idempotent: a no-op re-copy if it's already there).
#   4. `scripts/e2e/chat_browser_smoke.sh` with
#      `CHAT_SMOKE_BASE_URL=https://homeai.local/` — runs the FULL chat
#      smoke suite (create/switch/delete/reopen, Stop, HITL, edit/fork,
#      thinking on/off, and — the M9-specific steps this gate exists to
#      prove — markdown rendering (M9-01), the turn activity panel
#      (M9-02), the file: link opening the Files tab (M9-03), and the
#      voice-input mic over a secure context (M9-06)) against
#      `https://homeai.local` end to end, with the exported CA trusted in
#      the Playwright browser context (per M9-05's own ticket wording).
#
# `curl` is NOT installed on this host (same finding as every other
# `scripts/e2e/*.sh` script) — this script itself makes no direct HTTP
# calls; `verify_network.sh`/`export-ca.sh`/`chat_browser_smoke.sh` each
# use `wget`/Python/Playwright internally.
#
# Usage:
#   scripts/e2e/gate_m9.sh
#
# Exits non-zero (and prints the failing step) if any check fails.
# Safe to re-run — each sub-script cleans up its own thread/session/file;
# `export-ca.sh` is a no-op re-copy if the CA is already exported.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$REPO_ROOT"

API_BASE="http://localhost/api"
MODEL_RUNNER_HEALTHY_TIMEOUT_S=600
API_HEALTH_TIMEOUT_S=120

FILES_DIR="$(sed -n 's/^FILES_DIR=\(.*\)$/\1/p' .env | head -n1 | xargs)"
if [ -z "$FILES_DIR" ]; then
  echo "[gate-m9] ERROR: FILES_DIR not set in .env" >&2
  exit 1
fi
export FILES_DIR

BACKUP_DIR="$(sed -n 's/^BACKUP_DIR=\(.*\)$/\1/p' .env | head -n1 | xargs)"
BACKUP_DIR="${BACKUP_DIR:-/srv/homeai/backups}"

log() {
  echo "[gate-m9] $(date '+%H:%M:%S') $*"
}

# ---- health helpers (same pattern as every other scripts/e2e/*.sh script) --

http_ok() {
  wget -q -O /dev/null --timeout=10 --tries=1 "$1" >/dev/null 2>&1
}

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

step_stack_up_and_healthy() {
  log "Step 1/4: bringing up the full compose stack..."
  docker compose up -d --build
  wait_for_model_runner_healthy
  log "Waiting for agent-server API health (timeout ${API_HEALTH_TIMEOUT_S}s)..."
  if ! wait_for_api_health "$API_HEALTH_TIMEOUT_S"; then
    log "ERROR: ${API_BASE}/health never came up within ${API_HEALTH_TIMEOUT_S}s"
    return 1
  fi
  log "OK: model-runner healthy + ${API_BASE}/health OK"
}

step_verify_network() {
  log "Step 2/4: scripts/verify_network.sh (needs sudo)..."
  if [ "${EUID}" -eq 0 ]; then
    bash "${REPO_ROOT}/scripts/verify_network.sh"
  else
    sudo bash "${REPO_ROOT}/scripts/verify_network.sh"
  fi
}

step_export_ca() {
  log "Step 3/4: scripts/export-ca.sh (Caddy local CA for the HTTPS smoke)..."
  bash "${REPO_ROOT}/scripts/export-ca.sh"
}

step_chat_browser_smoke_https() {
  log "Step 4/4: scripts/e2e/chat_browser_smoke.sh over https://homeai.local (markdown, activity panel, file links, voice)..."
  CHAT_SMOKE_BASE_URL="https://homeai.local/" \
    CHAT_SMOKE_CA="${BACKUP_DIR}/homeai-root-ca.crt" \
    bash "${SCRIPT_DIR}/chat_browser_smoke.sh"
}

main() {
  log "=== GATE M9 (G9): chat experience from a phone (markdown, activity panel, file links, keyboard, voice) ==="
  step_stack_up_and_healthy
  step_verify_network
  step_export_ca
  step_chat_browser_smoke_https
  echo "GATE M9: PASS"
}

main

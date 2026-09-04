#!/usr/bin/env bash
# verify_egress.sh — M7-02: verify the egress-proxy's GET/HEAD-only filter +
# destination guard against the LIVE stack, over the REAL public internet
# (this is the one verification script in the M7 series that needs genuine
# outbound connectivity — there is no meaningful way to fake "does mitmproxy
# actually let a real GET through to example.com" without one).
#
# 6 checks, run in this order:
#   1. GET https://example.com/ through the proxy -> 200 (the happy path:
#      HTTPS is actually intercepted/decrypted/re-forwarded, not just
#      tunneled — proves the CA trust + MITM termination works end to end).
#   2. POST https://example.com/ through the proxy -> 403 (method guard).
#   3. GET http://192.168.1.1/ through the proxy -> 403 (destination guard:
#      RFC1918 private IP).
#   4. GET http://agent-server:8000/api/health through the proxy -> 403
#      (destination guard: bare hostname AND it happens to be a real
#      internal-only service — proves the proxy can't be used as a side
#      channel back into homeai-internal).
#   5. GET https://example.com:8443/ through the proxy -> 403 (destination
#      guard: port other than 80/443 — denied by `policy.py` before ever
#      attempting to actually connect to :8443, so this doesn't depend on
#      example.com listening there at all).
#   6. From INSIDE the already-running `agent-server` container (no proxy
#      configured — M7-03/M7-04 haven't wired that up yet), a raw connect
#      to example.com:443 fails with no route (re-confirms M7-01's
#      `homeai-internal`/`internal: true` guarantee still holds unchanged —
#      egress-proxy joining `homeai-net` doesn't leak a route to anyone
#      else on `homeai-internal`).
#
# All 6 checks run from a throwaway `curlimages/curl` container (checks 1-5)
# joined to `homeai-internal` (the same network `agent-server` itself will
# eventually reach `egress-proxy` from, once M7-03/M7-04 wire that up) with
# `HTTPS_PROXY=http://egress-proxy:8080` and the `egress-proxy-ca` named
# volume mounted read-only at `/ca` so `curl --cacert
# /ca/mitmproxy-ca-cert.pem` trusts the proxy's MITM leaf certs — the exact
# mount + trust recipe M7-03/M7-04 should follow for their real fetcher
# images (see docs/ARCHITECTURE.md §5's "CA handling" for the precise
# steps). Check 6 instead `docker exec`s directly into the live
# `agent-server` container.
#
# Requires the full stack up (`docker compose up -d --build`) with real
# host internet egress — this is NOT run as part of CI/sandbox verification
# (no fake/offline mode, unlike scripts/verify_isolation.sh's
# manager-mediated checks) since the entire point is proving the MITM
# actually works against a real HTTPS origin.
#
# Any check failing prints in RED and the suite continues (same convention
# as verify_network.sh/verify_isolation.sh) then exits 1 at the end if
# anything failed. Safe to re-run: the runner container is removed in an
# EXIT trap.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "$REPO_ROOT"

RUNNER_NAME="verify-egress-runner-$$"
RUNNER_IMAGE="curlimages/curl:latest"
RUNNER_STARTED=0

RED=$'\033[0;31m'
GREEN=$'\033[0;32m'
NC=$'\033[0m'

PASS_COUNT=0
FAIL_COUNT=0
FAILED_CHECKS=()

log() {
  echo "[verify-egress] $(date '+%H:%M:%S') $*"
}

pass() {
  PASS_COUNT=$((PASS_COUNT + 1))
  printf '%sPASS%s [%d] %s\n' "$GREEN" "$NC" "$1" "$2"
}

fail() {
  FAIL_COUNT=$((FAIL_COUNT + 1))
  FAILED_CHECKS+=("$1")
  printf '%sFAIL%s [%d] %s\n' "$RED" "$NC" "$1" "$2"
  if [ -n "${3:-}" ]; then
    printf '%s       -> %s%s\n' "$RED" "$3" "$NC"
  fi
}

preflight() {
  log "Resolving compose network ('homeai-internal') and volume ('egress-proxy-ca') names ..."
  local cfg
  cfg="$(docker compose config --format json)"

  NETWORK_NAME="$(python3 -c "
import json, sys
print(json.loads(sys.argv[1])['networks']['homeai-internal']['name'])
" "$cfg")"
  if ! docker network ls --format '{{.Name}}' | grep -qx "$NETWORK_NAME"; then
    log "ERROR: resolved network '${NETWORK_NAME}' not found via 'docker network ls' - is the stack up?"
    exit 1
  fi
  log "OK: using compose network '${NETWORK_NAME}'"

  VOLUME_NAME="$(python3 -c "
import json, sys
print(json.loads(sys.argv[1])['volumes']['egress-proxy-ca']['name'])
" "$cfg")"
  if ! docker volume ls --format '{{.Name}}' | grep -qx "$VOLUME_NAME"; then
    log "ERROR: resolved volume '${VOLUME_NAME}' not found via 'docker volume ls' - has egress-proxy started at least once (it generates the CA on first start)?"
    exit 1
  fi
  log "OK: using compose volume '${VOLUME_NAME}'"

  if [ -z "$(docker compose ps -q egress-proxy 2>/dev/null)" ]; then
    log "ERROR: egress-proxy is not running - is the stack up? (docker compose up -d --build egress-proxy)"
    exit 1
  fi
}

start_runner() {
  log "Starting runner container (${RUNNER_NAME}) on ${NETWORK_NAME}, CA mounted read-only at /ca ..."
  docker run -d --rm --name "$RUNNER_NAME" \
    --network "$NETWORK_NAME" \
    --entrypoint sleep \
    -v "${VOLUME_NAME}:/ca:ro" \
    -e "HTTPS_PROXY=http://egress-proxy:8080" \
    -e "HTTP_PROXY=http://egress-proxy:8080" \
    "$RUNNER_IMAGE" infinity >/dev/null
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
  local ca_tries=0
  while ! docker exec "$RUNNER_NAME" test -f /ca/mitmproxy-ca-cert.pem >/dev/null 2>&1; do
    ca_tries=$((ca_tries + 1))
    if [ "$ca_tries" -ge 20 ]; then
      log "ERROR: /ca/mitmproxy-ca-cert.pem never appeared - has egress-proxy generated its CA yet? (check 'docker compose logs egress-proxy')"
      exit 1
    fi
    sleep 1
  done
  log "OK: runner is exec-able and the CA cert is present"
}

cleanup() {
  if [ "$RUNNER_STARTED" = "1" ]; then
    docker rm -f "$RUNNER_NAME" >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT

# $1: curl args (as a single string, split by the shell inside the
# container - fine here, every call site below is a fixed literal, never
# attacker/user-controlled). Prints "<http_code>" on the last line
# (curl -w), or "000"/a curl exit code note if the connection itself failed
# outright (curl's own -w still prints on transport failure in most cases,
# but `|| echo 000` covers the case where curl exits before writing
# anything, e.g. our own denied-CONNECT probes).
runner_curl_status() {
  local args="$1"
  docker exec "$RUNNER_NAME" sh -c "curl -s -o /dev/null -w '%{http_code}' --cacert /ca/mitmproxy-ca-cert.pem --max-time 15 ${args} || echo 000"
}

check_1() {
  local status
  status="$(runner_curl_status "https://example.com/")"
  if [ "$status" = "200" ]; then
    pass 1 "GET https://example.com/ through egress-proxy -> 200"
  else
    fail 1 "GET https://example.com/ through egress-proxy -> 200" "got status ${status}"
  fi
}

check_2() {
  local status
  status="$(runner_curl_status "-X POST https://example.com/")"
  if [ "$status" = "403" ]; then
    pass 2 "POST https://example.com/ through egress-proxy -> 403 (method guard)"
  else
    fail 2 "POST https://example.com/ through egress-proxy -> 403 (method guard)" "got status ${status}"
  fi
}

check_3() {
  local status
  status="$(runner_curl_status "http://192.168.1.1/")"
  if [ "$status" = "403" ]; then
    pass 3 "GET http://192.168.1.1/ through egress-proxy -> 403 (RFC1918 destination guard)"
  else
    fail 3 "GET http://192.168.1.1/ through egress-proxy -> 403 (RFC1918 destination guard)" "got status ${status}"
  fi
}

check_4() {
  local status
  status="$(runner_curl_status "http://agent-server:8000/api/health")"
  if [ "$status" = "403" ]; then
    pass 4 "GET http://agent-server:8000/api/health through egress-proxy -> 403 (bare-hostname/internal destination guard)"
  else
    fail 4 "GET http://agent-server:8000/api/health through egress-proxy -> 403 (bare-hostname/internal destination guard)" "got status ${status}"
  fi
}

check_5() {
  local status
  status="$(runner_curl_status "https://example.com:8443/")"
  if [ "$status" = "403" ]; then
    pass 5 "GET https://example.com:8443/ through egress-proxy -> 403 (non-80/443 port destination guard)"
  else
    fail 5 "GET https://example.com:8443/ through egress-proxy -> 403 (non-80/443 port destination guard)" "got status ${status}"
  fi
}

check_6() {
  local cid out rc
  cid="$(docker compose ps -q agent-server 2>/dev/null)"
  if [ -z "$cid" ]; then
    fail 6 "agent-server (no proxy configured) cannot connect to example.com:443 (homeai-internal, M7-01 unaffected)" \
      "agent-server container not running - is the stack up?"
    return
  fi
  if out="$(docker exec "$cid" python3 -c "
import socket, sys
try:
    socket.create_connection(('example.com', 443), 3)
    sys.exit(1)  # connected -- unexpected, M7-01's no-egress guarantee broke
except Exception as e:
    print(f'blocked as expected: {e}')
    sys.exit(0)
" 2>&1)"; then
    rc=0
  else
    rc=$?
  fi
  if [ "$rc" -eq 0 ]; then
    pass 6 "agent-server (no proxy configured) cannot connect to example.com:443 (homeai-internal, M7-01 unaffected) -- ${out}"
  else
    fail 6 "agent-server (no proxy configured) cannot connect to example.com:443 (homeai-internal, M7-01 unaffected)" "$out"
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
  log "=== M7-02: egress-proxy verification suite ==="
  preflight
  start_runner

  check_1
  check_2
  check_3
  check_4
  check_5
  check_6

  summary
}

main
[ "$FAIL_COUNT" -eq 0 ]

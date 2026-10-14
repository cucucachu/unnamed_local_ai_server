#!/usr/bin/env bash
# verify_egress.sh — M7-02: verify the egress-proxy's GET/HEAD-only filter +
# destination guard against the LIVE stack, over the REAL public internet
# (this is the one verification script in the M7 series that needs genuine
# outbound connectivity — there is no meaningful way to fake "does mitmproxy
# actually let a real GET through to example.com" without one).
#
# 19 checks: 1-6 below (M7-02), 7-19 the integrations gateway's rules
# (M18-05 and the OAuth endpoints of §5.1, see check_gateway_rules). In order:
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
# Both the uppercase (HTTP_PROXY/HTTPS_PROXY) and lowercase
# (http_proxy/https_proxy) forms of the proxy env vars are set on the
# runner container — not redundant. curl (like several other HTTP clients)
# deliberately ignores the uppercase HTTP_PROXY specifically for plain
# `http://` requests, as a mitigation for the "httpoxy" CGI vulnerability
# class (CVE-2016-5385) where a remote client could inject a `Proxy:`
# header that a CGI gateway turns into `HTTP_PROXY` in the environment.
# HTTPS_PROXY is unaffected (that vulnerability is HTTP-header-specific,
# and HTTPS requests go through a CONNECT tunnel regardless), which is why
# checks 1/2/5 (all `https://`) passed with only the uppercase var set
# while checks 3/4 (both `http://`) silently bypassed the proxy entirely —
# curl connected directly to the target instead of denying via the addon,
# so 192.168.1.1 (check 3) timed out with no proxy involved (curl's own
# `000` fallback) and agent-server:8000 (check 4, reachable directly since
# the runner and agent-server share `homeai-internal`) returned its real
# 200. Confirmed by hand with `docker run curlimages/curl ... http_proxy=...`
# (lowercase) against both targets: both return 403 once the proxy is
# actually in the path. `policy.py` itself was never the problem.
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
# shellcheck source=e2e/lib/auth.sh
source "$SCRIPT_DIR/e2e/lib/auth.sh"

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
    -e "https_proxy=http://egress-proxy:8080" \
    -e "http_proxy=http://egress-proxy:8080" \
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
  unregister_destinations
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

# --- M18-05: the integrations gateway's identity and per-integration rules ----
#
# Registers four throwaway destinations as `e2e-egress-*` integrations
# (straight into the platform's registry; removed in the EXIT trap):
#   pub   https://example.com/mcp        a public server
#   lan   http://<gateway container IP>:8000/health   a private-address exception
#         (a private address that answers, so passing is visible: 405 to POST)
#   loop  https://localtest.me/mcp       a public name that resolves to 127.0.0.1
#   pin   https://example.net/mcp        pinned to a certificate it doesn't have
#   oauth https://example.com/oauth/mcp  an OAuth server whose metadata named a
#         token endpoint on another host (https://www.iana.org/oauth/token)
# The gateway's credential (EGRESS_GATEWAY_TOKEN, read from .env and handed to
# the runner by name only) is what opens them.

E2E_SLUGS="'e2e-egress-pub','e2e-egress-lan','e2e-egress-loop','e2e-egress-pin','e2e-egress-oauth'"

register_destinations() {
  EGRESS_GATEWAY_TOKEN="$(_e2e_env_value EGRESS_GATEWAY_TOKEN "")"
  export EGRESS_GATEWAY_TOKEN
  if [ -z "$EGRESS_GATEWAY_TOKEN" ]; then
    log "ERROR: EGRESS_GATEWAY_TOKEN not set in .env (M18-05)"
    exit 1
  fi
  local gw_cid
  gw_cid="$(docker compose ps -q integrations-gateway 2>/dev/null)"
  LAN_IP="$(docker inspect -f '{{range .NetworkSettings.Networks}}{{.IPAddress}} {{end}}' "$gw_cid" 2>/dev/null | awk '{print $1}')"
  if [ -z "$LAN_IP" ]; then
    log "ERROR: integrations-gateway isn't running (its address is the private test destination)"
    exit 1
  fi
  log "Registering e2e-egress-* destinations ..."
  _e2e_psql homeai_platform "
    DELETE FROM integrations WHERE slug IN (${E2E_SLUGS});
    INSERT INTO integrations (slug, name, url, credential_model, auth_method, private_address, cert_fingerprint) VALUES
      ('e2e-egress-pub', 'e2e', 'https://example.com/mcp', 'none', 'none', false, NULL),
      ('e2e-egress-lan', 'e2e', 'http://${LAN_IP}:8000/health', 'none', 'none', true, NULL),
      ('e2e-egress-loop', 'e2e', 'https://localtest.me/mcp', 'none', 'none', false, NULL),
      ('e2e-egress-pin', 'e2e', 'https://example.net/mcp', 'none', 'none', false, repeat('0', 64));
    INSERT INTO integrations (slug, name, url, credential_model, auth_method, oauth) VALUES
      ('e2e-egress-oauth', 'e2e', 'https://example.com/oauth/mcp', 'per_user', 'oauth',
       '{\"summary\": null, \"endpoints\": [\"https://www.iana.org/oauth/token\"]}');
  " >/dev/null
  REGISTERED=1
  # The proxy caches rules for a few seconds and refetches early on a miss.
  sleep 2
}

unregister_destinations() {
  if [ "${REGISTERED:-0}" = "1" ]; then
    _e2e_psql homeai_platform "DELETE FROM integrations WHERE slug IN (${E2E_SLUGS});" >/dev/null 2>&1 || true
  fi
}

# $1: "gateway" (the gateway's credential), "wrong" (a wrong one) or "none";
# $2: URL. Prints "<http_code> <connect_code> <X-Egress-Denied or ->".
gateway_probe() {
  local who="$1" url="$2" auth=""
  case "$who" in
    gateway) auth='--proxy-user "gateway:$EGRESS_GATEWAY_TOKEN"' ;;
    wrong) auth='--proxy-user "gateway:not-the-token"' ;;
  esac
  docker exec -e EGRESS_GATEWAY_TOKEN "$RUNNER_NAME" sh -c "
    out=\$(curl -s -o /dev/null -D /tmp/h -X POST -H 'Content-Type: application/json' -d '{}' \
      --cacert /ca/mitmproxy-ca-cert.pem --max-time 15 ${auth} \
      -w '%{http_code} %{http_connect}' '${url}' 2>/dev/null)
    denied=\$(sed -n 's/^[Xx]-[Ee]gress-[Dd]enied: *\\([a-z_]*\\).*/\\1/p' /tmp/h 2>/dev/null | head -n1)
    echo \"\${out:-000 000} \${denied:--}\""
}

# $1 check number, $2 description, $3 who, $4 url, $5 expectation:
#   passes        reached past the proxy's rules (no X-Egress-Denied, tunnel not refused)
#   denied:<code> answered 403 with X-Egress-Denied: <code>
#   connect:<n>   the CONNECT itself answered <n>
#   status:<n>    answered <n> (today's rules, no gateway header)
expect_probe() {
  local n="$1" desc="$2" who="$3" url="$4" want="$5" got code connect denied
  got="$(gateway_probe "$who" "$url")"
  read -r code connect denied <<<"$got"
  local ok=0
  case "$want" in
    passes) [ "$denied" = "-" ] && [ "$connect" != "403" ] && [ "$connect" != "407" ] && [ "$code" != "000" ] && ok=1 ;;
    denied:*) [ "$code" = "403" ] && [ "$denied" = "${want#denied:}" ] && ok=1 ;;
    connect:*) [ "$connect" = "${want#connect:}" ] && ok=1 ;;
    status:*) [ "$code" = "${want#status:}" ] && [ "$denied" = "-" ] && ok=1 ;;
  esac
  if [ "$ok" = "1" ]; then
    pass "$n" "$desc ($got)"
  else
    fail "$n" "$desc" "expected ${want}, got: http=${code} connect=${connect} denied=${denied}"
  fi
}

check_gateway_rules() {
  expect_probe 7 "gateway POST https://example.com/mcp (registered) passes the proxy" \
    gateway "https://example.com/mcp" passes
  expect_probe 8 "gateway POST https://example.org/ (not registered) refused at CONNECT" \
    gateway "https://example.org/" connect:403
  expect_probe 9 "gateway POST https://example.com:8443/ (registered host, other port) refused" \
    gateway "https://example.com:8443/" connect:403
  expect_probe 10 "gateway POST http://${LAN_IP}:8000/health (private exception) passes the proxy" \
    gateway "http://${LAN_IP}:8000/health" passes
  expect_probe 11 "gateway POST http://${LAN_IP}:8001/health (same host, other port) refused" \
    gateway "http://${LAN_IP}:8001/health" denied:not_registered
  expect_probe 12 "gateway POST https://localtest.me/mcp (registered, resolves to loopback) refused" \
    gateway "https://localtest.me/mcp" connect:403
  expect_probe 13 "gateway POST https://example.net/mcp (pinned to another certificate) refused" \
    gateway "https://example.net/mcp" denied:certificate_changed
  expect_probe 14 "POST https://example.com/mcp without the gateway's credential still refused" \
    none "https://example.com/mcp" status:403
  expect_probe 15 "a wrong proxy credential is refused at CONNECT (407)" \
    wrong "https://example.com/mcp" connect:407
  expect_probe 19 "gateway POST https://www.iana.org/oauth/token (an OAuth server's token endpoint) passes" \
    gateway "https://www.iana.org/oauth/token" passes
}

check_services_still_read_only() {
  local out
  if out="$(docker compose exec -T web-fetch /app/.venv/bin/python -c "
import os, httpx
r = httpx.post('https://example.com/mcp', json={}, proxy=os.environ['EGRESS_PROXY_URL'], verify='/tmp/ca-bundle.pem', timeout=15)
print(r.status_code, r.headers.get('x-egress-denied', '-'))
" 2>&1)" && [ "$out" = "403 -" ]; then
    pass 16 "web-fetch's POST to a registered host is refused (${out})"
  else
    fail 16 "web-fetch's POST to a registered host is refused" "$out"
  fi
  if out="$(docker compose exec -T agent-server python3 -c "
import urllib.request, urllib.error
opener = urllib.request.build_opener(urllib.request.ProxyHandler({'http': 'http://egress-proxy:8080'}))
try:
    opener.open(urllib.request.Request('http://${LAN_IP}:8000/health', data=b'{}', method='POST'), timeout=15)
    print('allowed')
except urllib.error.HTTPError as e:
    print(e.code, e.headers.get('X-Egress-Denied', '-'))
" 2>&1)" && [ "$out" = "403 -" ]; then
    pass 17 "agent-server's POST to a registered private address is refused (${out})"
  else
    fail 17 "agent-server's POST to a registered private address is refused" "$out"
  fi
  if out="$(docker compose exec -T integrations-gateway /app/.venv/bin/python -c "
import httpx2, ssl
from app.config import Settings
from app.connector import proxy_url
s = Settings()
r = httpx2.post('https://example.com/mcp', json={}, proxy=proxy_url(s.egress_proxy_url, s.egress_gateway_token),
                verify=ssl.create_default_context(cafile='/tmp/ca-bundle.pem'), timeout=15)
print(r.status_code, r.headers.get('x-egress-denied', '-'))
" 2>&1)" && [ "${out##* }" = "-" ]; then
    pass 18 "the deployed gateway's own credential passes the proxy to a registered host (${out})"
  else
    fail 18 "the deployed gateway's own credential passes the proxy to a registered host" "$out"
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

  register_destinations
  check_gateway_rules
  check_services_still_read_only

  summary
}

main
[ "$FAIL_COUNT" -eq 0 ]

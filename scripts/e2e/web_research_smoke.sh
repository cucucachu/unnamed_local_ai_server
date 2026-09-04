#!/usr/bin/env bash
# M7-04 Tier A: `web-fetch`'s `GET /search` against the LIVE stack (real
# SearXNG, real public search engines, real egress-proxy) - the one thing
# this milestone's unit tests (mocked SearXNG, `services/web-fetch/tests/
# test_search.py`) structurally can't prove: that a real query round-trips
# through SearXNG -> the enabled GET-only engines -> egress-proxy -> the
# public internet and back with at least one usable result, AND that
# nothing in that path ever issued a non-GET/HEAD request (the whole point
# of restricting SearXNG to GET-only engines in the first place -
# `services/searxng/settings.yml`'s own header comment has the audited
# engine list).
#
# Two checks, run in this order:
#   1. `GET /search?q=llama.cpp` (via `web-fetch`, port 8000, reached from
#      INSIDE the already-running `agent-server` container - the same
#      "no host curl" workaround every other `scripts/e2e/*.sh` script in
#      this repo uses, see the note below) -> `200` with a `results` list
#      containing at least one entry whose `url` starts with `https://`.
#   2. `egress-proxy`'s own log for the run shows zero `POST` lines
#      (`grep -c ' POST '` against `docker compose logs egress-proxy`) -
#      every request SearXNG made on behalf of check 1 stayed within the
#      GET/HEAD-only policy egress-proxy enforces (docs/ARCHITECTURE.md
#      §5), proving the engine audit in settings.yml actually holds at
#      runtime and not just on paper.
#
# DEVIATION from the ticket's own suggested invocation ("via docker compose
# exec agent-server curl"): `agent-server`'s image (`python:3.12-slim` +
# uv, `services/agent-server/Dockerfile`) does not install `curl` (verified
# - same finding `files_rest_smoke.sh`/`gate_m2.sh`/etc. already documented
# for the HOST shell; it's true of this container image too). Uses
# `python3`/`urllib.request` instead, exactly like every other REST check
# in this repo's `scripts/e2e/` directory.
#
# Requires the full stack up with real host internet egress (same
# requirement as `scripts/verify_egress.sh` - this is NOT runnable
# offline/in a sandbox with no live docker+internet, by design: the whole
# point is proving a REAL query reaches REAL public search engines and
# comes back).
#
# Usage:
#   scripts/e2e/web_research_smoke.sh
#
# Exits non-zero (and prints the failing check) if either check fails.
# Safe to re-run - makes no persistent changes to the stack or workspace.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$REPO_ROOT"

API_BASE="http://localhost/api"
MODEL_RUNNER_HEALTHY_TIMEOUT_S=600
API_HEALTH_TIMEOUT_S=120
SEARCH_QUERY="llama.cpp"

log() {
  echo "[web-research-smoke] $(date '+%H:%M:%S') $*"
}

http_ok() {
  # NOTE: deliberately NOT `wget --spider` — see `gate_m2.sh`'s own note
  # (spider mode sends HEAD, and `/api/health` is GET-only).
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

# Polls web-fetch's own `/health` from INSIDE agent-server (same network,
# `homeai-internal`) - web-fetch has no host-published port to reach any
# other way, and its own startup can lag agent-server's (separate
# `entrypoint.sh` CA-wait loop, docs/ARCHITECTURE.md §5 point 4).
wait_for_web_fetch_health() {
  local timeout_s="$1"
  local deadline=$(( $(date +%s) + timeout_s ))
  while (( $(date +%s) < deadline )); do
    if docker compose exec -T agent-server python3 -c "
import urllib.request
urllib.request.urlopen('http://web-fetch:8000/health', timeout=5)
" >/dev/null 2>&1; then
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
  log "Waiting for web-fetch health (timeout ${API_HEALTH_TIMEOUT_S}s)..."
  if ! wait_for_web_fetch_health "$API_HEALTH_TIMEOUT_S"; then
    log "ERROR: web-fetch:8000/health never came up within ${API_HEALTH_TIMEOUT_S}s"
    return 1
  fi
  log "OK: model-runner healthy, agent-server + web-fetch health OK"
}

# Records the current line count of `docker compose logs egress-proxy`, so
# check 2 below can grep only the NEW lines produced by this run's own
# search request, not any earlier POST-method-guard exercise (e.g. a prior
# `scripts/verify_egress.sh` run against the same long-lived stack).
capture_egress_log_baseline() {
  EGRESS_LOG_BASELINE_LINES="$(docker compose logs egress-proxy 2>/dev/null | wc -l | tr -d ' ')"
  log "Egress-proxy log baseline: ${EGRESS_LOG_BASELINE_LINES} lines"
}

step_search_request() {
  log "Step 2/4: GET web-fetch:8000/search?q=${SEARCH_QUERY} (from inside agent-server)..."
  local out
  out="$(mktemp)"
  if ! docker compose exec -T agent-server python3 -c "
import json
import sys
import urllib.error
import urllib.parse
import urllib.request

url = 'http://web-fetch:8000/search?' + urllib.parse.urlencode({'q': '${SEARCH_QUERY}'})
try:
    with urllib.request.urlopen(url, timeout=30) as resp:
        print(resp.status)
        print(resp.read().decode())
except urllib.error.HTTPError as e:
    print(e.code)
    print(e.read().decode())
" >"$out" 2>&1; then
    log "ERROR: python3 request inside agent-server failed to run:"
    cat "$out"
    rm -f "$out"
    return 1
  fi

  local status body
  status="$(sed -n '1p' "$out")"
  body="$(sed -n '2p' "$out")"
  rm -f "$out"

  if [ "$status" != "200" ]; then
    log "ERROR: expected 200 from /search, got ${status}: ${body}"
    return 1
  fi

  local check_out has_https_result result_count
  check_out="$(python3 -c "
import json, sys
body = json.loads(sys.argv[1])
results = body.get('results', [])
has_https = any(r.get('url', '').startswith('https://') for r in results)
print(len(results))
print('true' if has_https else 'false')
" "$body")"
  result_count="$(sed -n '1p' <<<"$check_out")"
  has_https_result="$(sed -n '2p' <<<"$check_out")"

  if [ "$has_https_result" != "true" ]; then
    log "ERROR: no result with an https:// url found (result_count=${result_count}): ${body}"
    return 1
  fi
  log "OK: /search returned ${result_count} result(s), at least one https:// url"
}

step_egress_get_only() {
  log "Step 3/4: confirming egress-proxy saw zero POST requests for this run..."
  local new_lines post_count
  new_lines="$(docker compose logs egress-proxy 2>/dev/null | tail -n "+$((EGRESS_LOG_BASELINE_LINES + 1))")"
  post_count="$(printf '%s\n' "$new_lines" | grep -c ' POST ' || true)"

  if [ "$post_count" -ne 0 ]; then
    log "ERROR: egress-proxy log shows ${post_count} POST line(s) for this run:"
    printf '%s\n' "$new_lines" | grep ' POST ' || true
    return 1
  fi
  log "OK: egress-proxy log shows 0 POST lines for this run (GET-only engine audit holds at runtime)"
}

main() {
  log "=== WEB RESEARCH SMOKE (M7-04): web-fetch /search -> SearXNG -> egress-proxy (GET-only) ==="
  step_stack_up_and_healthy
  capture_egress_log_baseline
  step_search_request
  step_egress_get_only
  log "Step 4/4: no cleanup needed (no persistent state created)."
  echo "WEB RESEARCH SMOKE: PASS"
}

main

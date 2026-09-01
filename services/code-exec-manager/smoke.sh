#!/usr/bin/env bash
# M4-02 acceptance: the ticket's ad-hoc `docker run` + curl-sequence check,
# scripted for repeatability (Tier A "Ad-hoc run works end to end").
#
# This deliberately does NOT use `docker compose` - compose wiring for
# code-exec-manager is M4-03 (see `docker-compose.yml`'s own service-list
# comment). It builds the image, runs ONE standalone container by hand
# exactly like the ticket's own acceptance command, and drives it with the
# curl-equivalent sequence: ensure -> execute (`python3 -c 'print(6*7)'` ->
# stdout `42`) -> write a file -> verify it landed on the HOST -> delete.
#
# `curl` is NOT installed on this host (same finding as every other
# `scripts/e2e/*.sh` script) - uses `wget`/`python3 -m urllib.request`
# instead.
#
# Usage:
#   services/code-exec-manager/smoke.sh
#
# Exits non-zero (and prints the failing step) if any check fails. Safe to
# re-run: cleans up the manager container, any exec container it spins up,
# and the scratch file it writes, via an EXIT trap.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
cd "$REPO_ROOT"

IMAGE_TAG="homeai-exec-manager"
RUN_ID="$$-$(date +%s)"
MANAGER_CONTAINER_NAME="homeai-exec-manager-smoke-${RUN_ID}"
SESSION_ID="smoke-${RUN_ID}"
BASE_URL="http://127.0.0.1:8090"
SCRATCH_FILE_NAME="smoke-m4-02-${RUN_ID}.txt"
SCRATCH_FILE_CONTENT="hello-from-exec-container"
SERVER_READY_TIMEOUT_S=30

env_var() {
  local key="$1" default="$2" val=""
  if [[ -f .env ]]; then
    val="$(grep -E "^${key}=" .env | tail -n1 | cut -d= -f2- || true)"
  fi
  echo "${val:-${default}}"
}
WORKSPACE_DIR="$(env_var WORKSPACE_DIR /srv/homeai/workspace)"
HOMEAI_UID="$(env_var HOMEAI_UID 1000)"
HOMEAI_GID="$(env_var HOMEAI_GID 1000)"
HOST_SCRATCH_PATH="${WORKSPACE_DIR}/${SCRATCH_FILE_NAME}"

log() {
  echo "[m4-02-smoke] $(date '+%H:%M:%S') $*"
}

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
    with urllib.request.urlopen(req, timeout=30) as resp:
        print(resp.status)
        print(resp.read().decode())
except urllib.error.HTTPError as e:
    print(e.code)
    print(e.read().decode())
PY
}

json_field() {
  python3 -c "import json,sys; print(json.loads(sys.argv[1])[sys.argv[2]])" "$1" "$2"
}

wait_for_server_ready() {
  log "Waiting for code-exec-manager to accept requests (timeout ${SERVER_READY_TIMEOUT_S}s)..."
  local deadline=$(( $(date +%s) + SERVER_READY_TIMEOUT_S ))
  while (( $(date +%s) < deadline )); do
    if wget -q -O /dev/null --timeout=5 --tries=1 "${BASE_URL}/sessions" >/dev/null 2>&1; then
      log "OK: server is up."
      return 0
    fi
    sleep 1
  done
  log "ERROR: server never became reachable within ${SERVER_READY_TIMEOUT_S}s"
  docker logs "$MANAGER_CONTAINER_NAME" 2>&1 || true
  return 1
}

cleanup() {
  rest_request DELETE "${BASE_URL}/sessions/${SESSION_ID}" >/dev/null 2>&1 || true
  docker rm -f "$MANAGER_CONTAINER_NAME" >/dev/null 2>&1 || true
  # Defensive: if the manager container died before the DELETE above landed,
  # make sure the exec container it created doesn't linger.
  docker rm -f "homeai-exec-${SESSION_ID}" >/dev/null 2>&1 || true
  rm -f "$HOST_SCRATCH_PATH" 2>/dev/null || true
}
trap cleanup EXIT

main() {
  log "=== M4-02 smoke: ad-hoc docker run + ensure/execute/delete ==="

  log "Step 1/6: docker build -t ${IMAGE_TAG} services/code-exec-manager"
  docker build -t "$IMAGE_TAG" services/code-exec-manager

  log "Step 2/6: docker run (mounts docker.sock, publishes 127.0.0.1:8090)..."
  docker run -d --rm \
    --name "$MANAGER_CONTAINER_NAME" \
    -p 127.0.0.1:8090:8090 \
    -v /var/run/docker.sock:/var/run/docker.sock \
    -e "WORKSPACE_DIR=${WORKSPACE_DIR}" \
    -e "HOMEAI_UID=${HOMEAI_UID}" \
    -e "HOMEAI_GID=${HOMEAI_GID}" \
    "$IMAGE_TAG" >/dev/null
  wait_for_server_ready

  log "Step 3/6: POST /sessions/${SESSION_ID}/ensure"
  local resp status body created
  resp="$(rest_request POST "${BASE_URL}/sessions/${SESSION_ID}/ensure")"
  status="$(sed -n '1p' <<<"$resp")"
  body="$(sed -n '2p' <<<"$resp")"
  if [ "$status" != "200" ]; then
    log "ERROR: expected 200 from ensure, got ${status}: ${body}"
    return 1
  fi
  created="$(json_field "$body" created)"
  if [ "$created" != "True" ]; then
    log "ERROR: expected created=true on first ensure, got: ${body}"
    return 1
  fi
  log "OK: ensure created a fresh container (${body})"

  log "Step 4/6: POST /sessions/${SESSION_ID}/execute (python3 -c 'print(6*7)')"
  resp="$(rest_request POST "${BASE_URL}/sessions/${SESSION_ID}/execute" '{"command": "python3 -c \"print(6*7)\""}')"
  status="$(sed -n '1p' <<<"$resp")"
  body="$(sed -n '2p' <<<"$resp")"
  if [ "$status" != "200" ]; then
    log "ERROR: expected 200 from execute, got ${status}: ${body}"
    return 1
  fi
  local stdout
  stdout="$(json_field "$body" stdout)"
  if [ "$(printf '%s' "$stdout" | tr -d '[:space:]')" != "42" ]; then
    log "ERROR: expected stdout '42', got: ${body}"
    return 1
  fi
  log "OK: execute returned stdout=42 (${body})"

  log "Step 5/6: write a file in /workspace, verify it lands on the HOST at ${HOST_SCRATCH_PATH}"
  rm -f "$HOST_SCRATCH_PATH"
  resp="$(rest_request POST "${BASE_URL}/sessions/${SESSION_ID}/execute" "$(python3 -c "
import json
print(json.dumps({'command': 'printf %s ${SCRATCH_FILE_CONTENT} > /workspace/${SCRATCH_FILE_NAME}'}))
")")"
  status="$(sed -n '1p' <<<"$resp")"
  body="$(sed -n '2p' <<<"$resp")"
  if [ "$status" != "200" ]; then
    log "ERROR: expected 200 from write-file execute, got ${status}: ${body}"
    return 1
  fi
  if [ ! -f "$HOST_SCRATCH_PATH" ]; then
    log "ERROR: ${HOST_SCRATCH_PATH} does not exist on the host after the container wrote it"
    return 1
  fi
  local host_content
  host_content="$(<"$HOST_SCRATCH_PATH")"
  if [ "$host_content" != "$SCRATCH_FILE_CONTENT" ]; then
    log "ERROR: host file content '${host_content}' != expected '${SCRATCH_FILE_CONTENT}'"
    return 1
  fi
  log "OK: file written inside the exec container is visible on the host with the right content"

  log "Step 6/6: DELETE /sessions/${SESSION_ID}, verify the exec container is gone"
  resp="$(rest_request DELETE "${BASE_URL}/sessions/${SESSION_ID}")"
  status="$(sed -n '1p' <<<"$resp")"
  if [ "$status" != "204" ]; then
    log "ERROR: expected 204 from delete, got ${status}"
    return 1
  fi
  if docker ps -a --format '{{.Names}}' | grep -qx "homeai-exec-${SESSION_ID}"; then
    log "ERROR: homeai-exec-${SESSION_ID} still present in 'docker ps -a' after delete"
    return 1
  fi
  log "OK: exec container removed - 'docker ps -a' is clean"

  echo "M4-02 SMOKE: PASS"
}

main

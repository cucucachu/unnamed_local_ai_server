#!/usr/bin/env bash
# M3-03 acceptance: manual curl pass (Conventions & Contracts §5 "Files")
# against the live stack, scripted for repeatability, PLUS the ticket's
# "agent-visibility cross-check" (the "three consumers, one directory"
# invariant from README.md).
#
# `curl` is NOT installed on this host (verified, same as `gate_m2.sh`/
# `threads_rest_smoke.sh`/`persistence_smoke.sh`). Unlike those three
# scripts' plain JSON REST calls (`urllib.request` is enough there), file
# upload needs real `multipart/form-data` encoding, which `urllib.request`
# does not build for you — this script hand-encodes the multipart body in
# Python (stdlib only, no `requests` dependency assumed on the host) rather
# than reaching for `wget`, which has no multipart-POST support at all.
# `cmp` (unlike `curl`) IS installed on this host — verified with `which
# cmp` before relying on it below — so it's used directly for the
# byte-identical download check per the ticket's acceptance criterion,
# rather than a Python hash-comparison fallback.
#
# From a running (or freshly brought-up) compose stack, this script:
#   1. Brings up the full stack, waits for model-runner + agent-server
#      API health (same polling helpers as `gate_m2.sh`) — the full stack
#      (not just agent-server) is needed because step 7 below drives a real
#      WS chat turn.
#   2. Uploads a file via multipart `POST /api/files/upload`.
#   3. Confirms it appears via `GET /api/files`.
#   4. `ls`'s it on the HOST at the real `WORKSPACE_DIR` from `.env`.
#   5. Downloads it back via `GET /api/files/download` and confirms it's
#      byte-identical to the original with `cmp`.
#   6. Deletes it via `DELETE /api/files`, confirms it's gone from both the
#      list and the host filesystem.
#   7. Agent-visibility cross-check: drops a SEPARATE file directly onto the
#      host workspace directory (bypassing the REST API entirely), then
#      asks the agent over WS (`scripts/ws_smoke.py`) to list the workspace
#      root, and confirms the dropped-in filename appears in a `tool_end`
#      frame's `result_preview` — proof the agent, the files REST API, and
#      the host all see the exact same directory.
#   8. Cleans up both files it created so re-running this script is safe
#      (idempotent, trap on EXIT — mirrors `gate_m2.sh`'s own convention).
#
# Usage:
#   scripts/e2e/files_rest_smoke.sh
#
# Exits non-zero (and prints the failing step) if any check fails.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$REPO_ROOT"

API_BASE="http://localhost/api"

MODEL_RUNNER_HEALTHY_TIMEOUT_S=600
API_HEALTH_TIMEOUT_S=120
WS_TURN_TIMEOUT_S=90

RUN_ID="$$-$(date +%s)"
FILE_NAME="files-rest-smoke-${RUN_ID}.txt"
FILE_CONTENT="FILES-REST-SMOKE-OK ${RUN_ID}"
AGENT_VIS_FILE_NAME="agent-visibility-${RUN_ID}.txt"
AGENT_VIS_THREAD_ID="files-rest-smoke-${RUN_ID}"

WORKSPACE_DIR="$(sed -n 's/^WORKSPACE_DIR=\(.*\)$/\1/p' .env | head -n1 | xargs)"
if [ -z "$WORKSPACE_DIR" ]; then
  echo "[files-rest-smoke] ERROR: WORKSPACE_DIR not set in .env" >&2
  exit 1
fi
AGENT_VIS_HOST_PATH="${WORKSPACE_DIR}/${AGENT_VIS_FILE_NAME}"

# A dedicated scratch dir (not a bare `mktemp` file) so the local upload
# source's basename is exactly `$FILE_NAME` — the multipart filename
# `upload_file` below sends is derived from this path's basename, and it's
# what the server's `os.path.basename` sanitization stores it as.
LOCAL_SCRATCH_DIR="$(mktemp -d)"
LOCAL_UPLOAD_SRC="${LOCAL_SCRATCH_DIR}/${FILE_NAME}"
LOCAL_DOWNLOAD_DST="${LOCAL_SCRATCH_DIR}/downloaded-${FILE_NAME}"
printf '%s' "$FILE_CONTENT" >"$LOCAL_UPLOAD_SRC"

log() {
  echo "[files-rest-smoke] $(date '+%H:%M:%S') $*"
}

# ---- curl-equivalent helpers (curl not installed; wget is) ----------------

http_ok() {
  # NOTE: deliberately NOT `wget --spider` — see `gate_m2.sh`'s own note
  # (this wget's spider mode sends HEAD, and `/api/health` is GET-only).
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

# ---- REST helpers (Python's urllib — `curl` unavailable) -------------------

# Prints the HTTP status code on line 1, the raw response body on line 2.
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
    with urllib.request.urlopen(req, timeout=15) as resp:
        print(resp.status)
        print(resp.read().decode())
except urllib.error.HTTPError as e:
    print(e.code)
    print(e.read().decode())
PY
}

# $1: dir path (workspace-relative) to upload into. $2: local file to upload.
# Prints status on line 1, response body on line 2. Hand-builds the
# multipart body — `urllib.request` has no built-in multipart encoder, and
# `requests` isn't a guaranteed-installed dependency on this host's system
# python3 (only inside `services/agent-server`'s own `uv`-managed venv).
upload_file() {
  local target_dir="$1" local_path="$2"
  python3 - "${API_BASE}/files/upload" "$target_dir" "$local_path" <<'PY'
import mimetypes
import os
import sys
import urllib.error
import urllib.request

url, target_dir, local_path = sys.argv[1], sys.argv[2], sys.argv[3]
filename = os.path.basename(local_path)
with open(local_path, "rb") as f:
    content = f.read()

boundary = "HomeAIFilesRestSmokeBoundary"
content_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"


def text_field(name: str, value: str) -> bytes:
    return (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="{name}"\r\n\r\n'
        f"{value}\r\n"
    ).encode()


def file_field(name: str, filename: str, content: bytes, content_type: str) -> bytes:
    header = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="{name}"; filename="{filename}"\r\n'
        f"Content-Type: {content_type}\r\n\r\n"
    ).encode()
    return header + content + b"\r\n"


body = (
    text_field("path", target_dir)
    + file_field("file", filename, content, content_type)
    + f"--{boundary}--\r\n".encode()
)

req = urllib.request.Request(
    url,
    data=body,
    method="POST",
    headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
)
try:
    with urllib.request.urlopen(req, timeout=30) as resp:
        print(resp.status)
        print(resp.read().decode())
except urllib.error.HTTPError as e:
    print(e.code)
    print(e.read().decode())
PY
}

# $1: workspace-relative file path. $2: local destination path. Prints the
# status code on line 1; writes the raw response body bytes to $2.
download_file() {
  local remote_path="$1" local_dst="$2"
  python3 - "${API_BASE}/files/download" "$remote_path" "$local_dst" <<'PY'
import sys
import urllib.error
import urllib.parse
import urllib.request

url_base, remote_path, local_dst = sys.argv[1], sys.argv[2], sys.argv[3]
url = f"{url_base}?{urllib.parse.urlencode({'path': remote_path})}"
try:
    with urllib.request.urlopen(url, timeout=30) as resp:
        with open(local_dst, "wb") as f:
            f.write(resp.read())
        print(resp.status)
except urllib.error.HTTPError as e:
    print(e.code)
PY
}

# $1: base REST path (e.g. "/api/files"). $2: "path" query param value.
url_with_path_param() {
  python3 -c "
import sys, urllib.parse
print(sys.argv[1] + '?' + urllib.parse.urlencode({'path': sys.argv[2]}))
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
  log "Step 1/8: bringing up the full compose stack..."
  docker compose up -d --build
  wait_for_model_runner_healthy
  log "Waiting for agent-server API health (timeout ${API_HEALTH_TIMEOUT_S}s)..."
  if ! wait_for_api_health "$API_HEALTH_TIMEOUT_S"; then
    log "ERROR: ${API_BASE}/health never came up within ${API_HEALTH_TIMEOUT_S}s"
    return 1
  fi
  log "OK: model-runner healthy + ${API_BASE}/health OK"
}

step_upload() {
  log "Step 2/8: POST /api/files/upload (${FILE_NAME}, workspace root)..."
  local resp status body
  resp="$(upload_file "" "$LOCAL_UPLOAD_SRC")"
  status="$(sed -n '1p' <<<"$resp")"
  body="$(sed -n '2p' <<<"$resp")"

  if [ "$status" != "201" ]; then
    log "ERROR: expected 201, got ${status}: ${body}"
    return 1
  fi
  local uploaded
  uploaded="$(python3 -c "import json,sys; print(json.loads(sys.argv[1])['uploaded'][0])" "$body")"
  UPLOADED_NAME="$uploaded"
  if [ "$UPLOADED_NAME" != "$FILE_NAME" ]; then
    log "ERROR: expected uploaded name '${FILE_NAME}', got '${UPLOADED_NAME}'"
    return 1
  fi
  log "OK: uploaded as '${UPLOADED_NAME}'"
}

step_appears_in_list() {
  log "Step 3/8: GET /api/files - confirm '${UPLOADED_NAME}' is present..."
  local resp status body present
  resp="$(rest_request GET "${API_BASE}/files")"
  status="$(sed -n '1p' <<<"$resp")"
  body="$(sed -n '2p' <<<"$resp")"

  if [ "$status" != "200" ]; then
    log "ERROR: expected 200, got ${status}: ${body}"
    return 1
  fi
  present="$(json_entries_contains_name "$body" "$UPLOADED_NAME")"
  if [ "$present" != "true" ]; then
    log "ERROR: '${UPLOADED_NAME}' not found in list: ${body}"
    return 1
  fi
  log "OK: '${UPLOADED_NAME}' present in the list"
}

step_visible_on_host() {
  log "Step 4/8: ls on the host at ${WORKSPACE_DIR}/${UPLOADED_NAME}..."
  local host_path="${WORKSPACE_DIR}/${UPLOADED_NAME}"
  if [ ! -f "$host_path" ]; then
    log "ERROR: ${host_path} does not exist on the host"
    ls -la "$WORKSPACE_DIR"
    return 1
  fi
  if ! cmp -s "$LOCAL_UPLOAD_SRC" "$host_path"; then
    log "ERROR: ${host_path} content does not match what was uploaded"
    return 1
  fi
  log "OK: ${host_path} exists on the host with the uploaded content"
}

step_download_byte_identical() {
  log "Step 5/8: GET /api/files/download - confirm byte-identical via cmp..."
  local status
  status="$(download_file "$UPLOADED_NAME" "$LOCAL_DOWNLOAD_DST")"
  if [ "$status" != "200" ]; then
    log "ERROR: expected 200 from download, got ${status}"
    return 1
  fi
  if ! cmp -s "$LOCAL_UPLOAD_SRC" "$LOCAL_DOWNLOAD_DST"; then
    log "ERROR: downloaded content differs from the uploaded original (cmp mismatch)"
    return 1
  fi
  log "OK: downloaded file is byte-identical to the upload (cmp)"
}

step_delete_and_confirm_gone() {
  log "Step 6/8: DELETE /api/files - confirm gone from list + host fs..."
  local resp status body

  resp="$(rest_request DELETE "$(url_with_path_param "${API_BASE}/files" "$UPLOADED_NAME")")"
  status="$(sed -n '1p' <<<"$resp")"
  if [ "$status" != "204" ]; then
    log "ERROR: expected 204 from DELETE, got ${status}"
    return 1
  fi

  resp="$(rest_request GET "${API_BASE}/files")"
  status="$(sed -n '1p' <<<"$resp")"
  body="$(sed -n '2p' <<<"$resp")"
  if [ "$(json_entries_contains_name "$body" "$UPLOADED_NAME")" != "false" ]; then
    log "ERROR: '${UPLOADED_NAME}' still present in list after DELETE: ${body}"
    return 1
  fi
  if [ -e "${WORKSPACE_DIR}/${UPLOADED_NAME}" ]; then
    log "ERROR: ${WORKSPACE_DIR}/${UPLOADED_NAME} still exists on the host after DELETE"
    return 1
  fi
  log "OK: '${UPLOADED_NAME}' gone from the list and from the host filesystem"
}

step_agent_visibility_cross_check() {
  log "Step 7/8: agent-visibility cross-check (drop file on host -> agent ls over WS)..."
  printf 'dropped straight onto the host workspace dir\n' >"$AGENT_VIS_HOST_PATH"

  local out
  out="$(mktemp)"
  if ! WS_SMOKE_THREAD_ID="$AGENT_VIS_THREAD_ID" \
      WS_SMOKE_PROMPT="List the files in the workspace root directory using your ls tool." \
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

  # Frames are printed one Python-dict-repr per line (see `ws_smoke.py`) -
  # a `tool_end` frame whose own line also contains the dropped-in filename
  # is exactly "the file name appears in a tool result frame" from the
  # ticket's acceptance criterion.
  if ! grep "'type': 'tool_end'" "$out" | grep -q "$AGENT_VIS_FILE_NAME"; then
    log "ERROR: no tool_end frame mentioned '${AGENT_VIS_FILE_NAME}':"
    cat "$out"
    rm -f "$out"
    return 1
  fi
  log "OK: agent's ls tool_end result_preview mentions '${AGENT_VIS_FILE_NAME}'"
  rm -f "$out"
}

cleanup() {
  # Always runs (success or failure) so the script is safely re-runnable.
  rm -f "${WORKSPACE_DIR}/${UPLOADED_NAME:-$FILE_NAME}" "$AGENT_VIS_HOST_PATH" 2>/dev/null || true
  rm -rf "$LOCAL_SCRATCH_DIR" 2>/dev/null || true
}
trap cleanup EXIT

main() {
  log "=== FILES REST SMOKE (M3-03): upload -> list -> host ls -> download -> delete -> agent visibility ==="
  step_stack_up_and_healthy
  step_upload
  step_appears_in_list
  step_visible_on_host
  step_download_byte_identical
  step_delete_and_confirm_gone
  step_agent_visibility_cross_check
  log "Step 8/8: cleanup (via EXIT trap)."
  echo "FILES REST SMOKE: PASS"
}

main

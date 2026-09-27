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
#   2. Uploads a file into the user's Personal space via multipart
#      `POST /api/platform/files/upload` (M11-01: the platform files API).
#   3. Confirms it appears via `GET /api/platform/files?path=/personal`.
#   4. Checks it on disk inside the platform container
#      (`/data/spaces/<personal id>/files/`): same bytes, owned
#      `<user uid>:<personal space gid>`, mode 0660.
#   5. Downloads it back via `GET /api/platform/files/download` and confirms
#      it's byte-identical to the original with `cmp`.
#   6. Deletes it via `DELETE /api/platform/files`, confirms it's gone from
#      both the list and the disk.
#   7. Agent-visibility cross-check: drops a SEPARATE file directly onto the
#      host files directory (bypassing the REST API entirely), then
#      asks the agent over WS (`scripts/ws_smoke.py`) to list the files
#      root, and confirms the dropped-in filename appears in a `tool_end`
#      frame's `result_preview` — proof the agent and the host see the same
#      directory. Until M11-02 moves the agent onto spaces, the agent still
#      works in `FILES_DIR`, not in the space steps 2-6 used.
#   8. Cleans up both files it created so re-running this script is safe
#      (idempotent, trap on EXIT — mirrors `gate_m2.sh`'s own convention).
#
# M10-04: runs signed in (`lib/auth.sh`); the step-7 thread is created via
# `POST /api/threads` since the chat socket only accepts owned threads.
#
# Usage:
#   scripts/e2e/files_rest_smoke.sh
#
# Exits non-zero (and prints the failing step) if any check fails.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$REPO_ROOT"
# shellcheck source=lib/auth.sh
source "$SCRIPT_DIR/lib/auth.sh"

API_BASE="http://localhost/api"

MODEL_RUNNER_HEALTHY_TIMEOUT_S=600
API_HEALTH_TIMEOUT_S=120
WS_TURN_TIMEOUT_S=90

RUN_ID="$$-$(date +%s)"
FILE_NAME="files-rest-smoke-${RUN_ID}.txt"
FILE_CONTENT="FILES-REST-SMOKE-OK ${RUN_ID}"
FILES_API="${API_BASE}/platform/files"
# Set in step 2: the uploaded file's virtual path and its path in the
# platform container.
UPLOADED_PATH=""
UPLOADED_DISK_PATH=""
AGENT_VIS_FILE_NAME="agent-visibility-${RUN_ID}.txt"
# Created in step 7.
AGENT_VIS_THREAD_ID=""

FILES_DIR="$(sed -n 's/^FILES_DIR=\(.*\)$/\1/p' .env | head -n1 | xargs)"
if [ -z "$FILES_DIR" ]; then
  echo "[files-rest-smoke] ERROR: FILES_DIR not set in .env" >&2
  exit 1
fi
AGENT_VIS_HOST_PATH="${FILES_DIR}/${AGENT_VIS_FILE_NAME}"

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
import os
import sys
import urllib.error
import urllib.request

method, url, json_body = sys.argv[1], sys.argv[2], sys.argv[3]
data = json_body.encode() if json_body else None
headers = {"Content-Type": "application/json"} if data else {}
headers["Cookie"] = os.environ["E2E_AUTH_COOKIE"]
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

# $1: virtual dir path to upload into. $2: local file to upload.
# Prints status on line 1, response body on line 2. Hand-builds the
# multipart body — `urllib.request` has no built-in multipart encoder, and
# `requests` isn't a guaranteed-installed dependency on this host's system
# python3 (only inside `services/agent-server`'s own `uv`-managed venv).
upload_file() {
  local target_dir="$1" local_path="$2"
  python3 - "${FILES_API}/upload" "$target_dir" "$local_path" <<'PY'
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
    headers={
        "Content-Type": f"multipart/form-data; boundary={boundary}",
        "Cookie": os.environ["E2E_AUTH_COOKIE"],
    },
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

# $1: virtual file path. $2: local destination path. Prints the
# status code on line 1; writes the raw response body bytes to $2.
download_file() {
  local remote_path="$1" local_dst="$2"
  python3 - "${FILES_API}/download" "$remote_path" "$local_dst" <<'PY'
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

url_base, remote_path, local_dst = sys.argv[1], sys.argv[2], sys.argv[3]
url = f"{url_base}?{urllib.parse.urlencode({'path': remote_path})}"
req = urllib.request.Request(url, headers={"Cookie": os.environ["E2E_AUTH_COOKIE"]})
try:
    with urllib.request.urlopen(req, timeout=30) as resp:
        with open(local_dst, "wb") as f:
            f.write(resp.read())
        print(resp.status)
except urllib.error.HTTPError as e:
    print(e.code)
PY
}

# $1: base REST URL (e.g. "$FILES_API"). $2: "path" query param value.
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
  log "Step 2/8: POST /api/platform/files/upload (${FILE_NAME} into /personal)..."
  local resp status body
  resp="$(upload_file "/personal" "$LOCAL_UPLOAD_SRC")"
  status="$(sed -n '1p' <<<"$resp")"
  body="$(sed -n '2p' <<<"$resp")"

  if [ "$status" != "201" ]; then
    log "ERROR: expected 201, got ${status}: ${body}"
    return 1
  fi
  UPLOADED_PATH="$(python3 -c "import json,sys; print(json.loads(sys.argv[1])['uploaded'][0])" "$body")"
  if [ "$UPLOADED_PATH" != "/personal/${FILE_NAME}" ]; then
    log "ERROR: expected uploaded path '/personal/${FILE_NAME}', got '${UPLOADED_PATH}'"
    return 1
  fi
  log "OK: uploaded as '${UPLOADED_PATH}'"
}

step_appears_in_list() {
  log "Step 3/8: GET /api/platform/files?path=/personal - confirm '${FILE_NAME}' is present..."
  local resp status body present
  resp="$(rest_request GET "$(url_with_path_param "$FILES_API" /personal)")"
  status="$(sed -n '1p' <<<"$resp")"
  body="$(sed -n '2p' <<<"$resp")"

  if [ "$status" != "200" ]; then
    log "ERROR: expected 200, got ${status}: ${body}"
    return 1
  fi
  present="$(json_entries_contains_name "$body" "$FILE_NAME")"
  if [ "$present" != "true" ]; then
    log "ERROR: '${FILE_NAME}' not found in list: ${body}"
    return 1
  fi
  log "OK: '${FILE_NAME}' present in the list"
}

step_on_disk() {
  log "Step 4/8: on disk in the platform container - content, owner, mode..."
  local resp body space_id space_gid uid want got
  resp="$(rest_request GET "${API_BASE}/platform/spaces")"
  body="$(sed -n '2p' <<<"$resp")"
  read -r space_id space_gid < <(python3 -c "
import json, sys
s = next(s for s in json.loads(sys.argv[1])['spaces'] if s['kind'] == 'personal')
print(s['id'], s['gid'])
" "$body")
  uid="$(_e2e_psql homeai_platform "SELECT uid FROM users WHERE username = '${E2E_AUTH_USER}'")"
  UPLOADED_DISK_PATH="/data/spaces/${space_id}/files/${FILE_NAME}"

  if ! docker compose exec -T platform cat "$UPLOADED_DISK_PATH" | cmp -s "$LOCAL_UPLOAD_SRC" -; then
    log "ERROR: ${UPLOADED_DISK_PATH} is missing or its content does not match what was uploaded"
    return 1
  fi
  want="${uid}:${space_gid} 660"
  got="$(docker compose exec -T platform stat -c '%u:%g %a' "$UPLOADED_DISK_PATH")"
  if [ "$got" != "$want" ]; then
    log "ERROR: ${UPLOADED_DISK_PATH} is '${got}', expected '${want}' (uid:gid mode)"
    return 1
  fi
  log "OK: ${UPLOADED_DISK_PATH} has the uploaded content, owner ${uid}:${space_gid}, mode 0660"
}

step_download_byte_identical() {
  log "Step 5/8: GET /api/platform/files/download - confirm byte-identical via cmp..."
  local status
  status="$(download_file "$UPLOADED_PATH" "$LOCAL_DOWNLOAD_DST")"
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
  log "Step 6/8: DELETE /api/platform/files - confirm gone from list + disk..."
  local resp status body

  resp="$(rest_request DELETE "$(url_with_path_param "$FILES_API" "$UPLOADED_PATH")")"
  status="$(sed -n '1p' <<<"$resp")"
  if [ "$status" != "204" ]; then
    log "ERROR: expected 204 from DELETE, got ${status}"
    return 1
  fi

  resp="$(rest_request GET "$(url_with_path_param "$FILES_API" /personal)")"
  body="$(sed -n '2p' <<<"$resp")"
  if [ "$(json_entries_contains_name "$body" "$FILE_NAME")" != "false" ]; then
    log "ERROR: '${FILE_NAME}' still present in list after DELETE: ${body}"
    return 1
  fi
  if docker compose exec -T platform test -e "$UPLOADED_DISK_PATH"; then
    log "ERROR: ${UPLOADED_DISK_PATH} still exists after DELETE"
    return 1
  fi
  UPLOADED_PATH=""
  log "OK: '${FILE_NAME}' gone from the list and from disk"
}

step_agent_visibility_cross_check() {
  log "Step 7/8: agent-visibility cross-check (drop file on host -> agent ls over WS)..."
  printf 'dropped straight onto the host files dir\n' >"$AGENT_VIS_HOST_PATH"

  local resp status body
  resp="$(rest_request POST "${API_BASE}/threads" '{}')"
  status="$(sed -n '1p' <<<"$resp")"
  body="$(sed -n '2p' <<<"$resp")"
  if [ "$status" != "201" ]; then
    log "ERROR: expected 201 from POST /api/threads, got ${status}: ${body}"
    return 1
  fi
  AGENT_VIS_THREAD_ID="$(python3 -c "import json,sys; print(json.loads(sys.argv[1])['id'])" "$body")"

  local out
  out="$(mktemp)"
  if ! WS_SMOKE_THREAD_ID="$AGENT_VIS_THREAD_ID" \
      WS_SMOKE_PROMPT="List the files in the root directory using your ls tool." \
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
  rm -f "$AGENT_VIS_HOST_PATH" 2>/dev/null || true
  if [ -n "$UPLOADED_PATH" ] && [ -n "${E2E_AUTH_COOKIE:-}" ]; then
    rest_request DELETE "$(url_with_path_param "$FILES_API" "$UPLOADED_PATH")" >/dev/null 2>&1 || true
  fi
  rm -rf "$LOCAL_SCRATCH_DIR" 2>/dev/null || true
  if [ -n "$AGENT_VIS_THREAD_ID" ]; then
    rest_request DELETE "${API_BASE}/threads/${AGENT_VIS_THREAD_ID}" >/dev/null 2>&1 || true
  fi
  e2e_auth_end
}
trap cleanup EXIT

main() {
  log "=== FILES REST SMOKE (M3-03): upload -> list -> on disk -> download -> delete -> agent visibility ==="
  step_stack_up_and_healthy
  e2e_auth_begin files
  step_upload
  step_appears_in_list
  step_on_disk
  step_download_byte_identical
  step_delete_and_confirm_gone
  step_agent_visibility_cross_check
  log "Step 8/8: cleanup (via EXIT trap)."
  echo "FILES REST SMOKE: PASS"
}

main

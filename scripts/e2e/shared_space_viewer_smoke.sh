#!/usr/bin/env bash
# M11-05 (GATE G11) real-model scenario: a shared space across the UI, the
# agent's file tools, and exec, for an owner and a viewer.
#
# Two throwaway `e2e-*` users (recovery CLI, `lib/auth.sh`): A owns shared
# space `e2e-g11-<hex>`, B is a viewer of it. A also has a private file in
# its personal space (planted through the files API).
#
#   1. A's agent writes `/spaces/<slug>/g11-note.md` (exact marker text) with
#      `write_file`. Asserted on the files API: A and B both read the marker.
#   2. B in a real browser (`shared_space_viewer_browser.mjs`): the Files
#      root lists Personal and the space; B's Personal doesn't show A's
#      private file; the space shows the note read-only (no upload/new
#      folder, only Download in the action sheet), and downloading it from
#      the UI yields the marker.
#   3. B's agent tries `write_file` into the space: every call is refused
#      ("Permission denied") and the space still holds only the note, with
#      the marker unchanged.
#   4. B's agent runs `execute_code` with a command that reads the note and
#      then tries to create a file in the space and append to the note: the
#      read works, both writes fail with "Read-only file system", the space
#      is unchanged, and B's exec container binds the space read-only.
#
# Assertions are on files API / container state, never on the model's
# prose. HITL is turned off for both users so tools run directly. Each
# model turn is retried at most once, and only when the model didn't make
# the tool call asked for; a write that lands where it must not fails
# immediately, no retry.
#
# Everything (users, personal spaces, the shared space, threads, exec
# containers) is deleted on exit. Never completes bootstrap.
#
# Needs the live stack with the real model: NOT runnable offline.
#
# Usage: scripts/e2e/shared_space_viewer_smoke.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$REPO_ROOT"
# shellcheck source=lib/auth.sh
source "$SCRIPT_DIR/lib/auth.sh"

HEX="$(openssl rand -hex 3)"
SPACE="e2e-g11-${HEX}"
SPACE_NAME="E2E G11 ${HEX}"
NOTE_NAME="g11-note.md"
MARKER="g11-shared-$(openssl rand -hex 4)"
PRIVATE_NAME="g11-private-$(openssl rand -hex 4).md"
THREADS_FILE="$(mktemp -t g11-threads.XXXXXX)"

log() { echo "[shared-space-viewer] $(date '+%H:%M:%S') $*"; }

cleanup() {
  local id ids
  while read -r id; do
    [[ "$id" =~ ^[0-9a-f-]{36}$ ]] && docker rm -f "homeai-exec-${id}" >/dev/null 2>&1 || true
  done <"$THREADS_FILE"
  rm -f "$THREADS_FILE"
  ids="$(_e2e_psql homeai_platform "DELETE FROM spaces WHERE slug = '$SPACE' AND kind = 'shared' RETURNING id" 2>/dev/null || true)"
  for id in $ids; do
    [[ "$id" =~ ^[0-9a-f-]{36}$ ]] && _e2e_compose exec -T platform rm -rf "/data/spaces/$id" || true
  done
  e2e_auth_end
}
trap cleanup EXIT

cli() { _e2e_compose exec -T platform python -m app.cli "$@" >/dev/null; }

e2e_auth_create_user g11-a
USER_A="$E2E_NEW_USER"
COOKIE_A="$(e2e_auth_login "$USER_A" "$E2E_NEW_PASSWORD")"
e2e_auth_create_user g11-b
USER_B="$E2E_NEW_USER"
PASSWORD_B="$E2E_NEW_PASSWORD"
COOKIE_B="$(e2e_auth_login "$USER_B" "$PASSWORD_B")"
cli create-space "$SPACE" --name "$SPACE_NAME" --owner "$USER_A"
cli add-member "$SPACE" "$USER_B" --role viewer
log "users: A=${USER_A} (owner) B=${USER_B} (viewer); space: /spaces/${SPACE}"

export COOKIE_A COOKIE_B SPACE NOTE_NAME MARKER PRIVATE_NAME THREADS_FILE

# argv: phase (`setup` | `owner-writes` | `viewer-refused`).
TURNS_PY="$(cat <<'PY'
import asyncio
import json
import os
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request

from websockets.asyncio.client import connect

BASE = os.environ.get("E2E_BASE", "http://localhost").rstrip("/")
A, B = os.environ["COOKIE_A"], os.environ["COOKIE_B"]
SPACE = os.environ["SPACE"]
SHARED = f"/spaces/{SPACE}"
NOTE_NAME = os.environ["NOTE_NAME"]
NOTE = f"{SHARED}/{NOTE_NAME}"
MARKER = os.environ["MARKER"]
PRIVATE = f"/personal/{os.environ['PRIVATE_NAME']}"
TURN_TIMEOUT_S = 240


def fail(msg: str) -> None:
    print(f"FAIL: {msg}", file=sys.stderr)
    sys.exit(1)


def api(cookie, method, path, body=None, raw=False):
    req = urllib.request.Request(
        f"{BASE}{path}",
        data=None if body is None else json.dumps(body).encode(),
        method=method,
        headers={"Cookie": cookie, "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = resp.read()
            return resp.status, (data if raw else json.loads(data or b"null"))
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def q(path: str) -> str:
    return urllib.parse.quote(path)


def listing(cookie, path) -> list[str]:
    status, body = api(cookie, "GET", f"/api/platform/files?path={q(path)}")
    if status != 200:
        fail(f"list {path}: HTTP {status} {body!r}")
    return sorted(e["name"] for e in body["entries"])


def content(cookie, path):
    status, body = api(cookie, "GET", f"/api/platform/files/download?path={q(path)}", raw=True)
    return status, (body.decode(errors="replace").strip() if status == 200 else body)


def space_untouched(context: str) -> None:
    """The space holds exactly A's note with A's marker - for A and for B."""
    for who, cookie in (("A", A), ("B", B)):
        names = listing(cookie, SHARED)
        if names != [NOTE_NAME]:
            fail(f"{context}: {SHARED} as {who} lists {names}, expected only {NOTE_NAME}")
        status, text = content(cookie, NOTE)
        if status != 200 or text != MARKER:
            fail(f"{context}: {NOTE} as {who}: HTTP {status} {text!r}, expected {MARKER!r}")


async def turn(who, cookie, prompt):
    status, thread = api(cookie, "POST", "/api/threads", {})
    if status != 201:
        fail(f"{who}: create thread: HTTP {status} {thread!r}")
    with open(os.environ["THREADS_FILE"], "a") as f:
        f.write(thread["id"] + "\n")
    ws_base = BASE.replace("http", "ws", 1)
    frames = []
    print(f"\n--- {who} (thread {thread['id']}): {prompt}")
    try:
        async with connect(
            f"{ws_base}/ws/chat/{thread['id']}", additional_headers={"Cookie": cookie}
        ) as ws:
            await ws.send(json.dumps({"type": "user_message", "content": prompt}))
            async with asyncio.timeout(TURN_TIMEOUT_S):
                async for raw in ws:
                    frame = json.loads(raw)
                    frames.append(frame)
                    if frame["type"] in ("turn_end", "error"):
                        break
    except TimeoutError:
        print(f"  (turn timed out after {TURN_TIMEOUT_S}s)")
    for f in frames:
        if f["type"] == "tool_start":
            print(f"  tool_start {f['name']} {json.dumps(f['args'])[:300]}")
        elif f["type"] == "tool_end":
            print(f"  tool_end   {f['name']} [{f['status']}] {f['result_preview'][:400]!r}")
        elif f["type"] in ("error", "approval_request"):
            print(f"  {f['type']}: {json.dumps(f)[:300]}")
    reply = "".join(f["content"] for f in frames if f["type"] == "token")
    print(f"  reply: {reply.strip()[:300]!r}")
    print(f"  end: {frames[-1] if frames else None}")
    return thread["id"], frames


def tool_starts(frames, name):
    return [f["args"] for f in frames if f["type"] == "tool_start" and f["name"] == name]


def tool_results(frames, name):
    return [f["result_preview"] for f in frames if f["type"] == "tool_end" and f["name"] == name]


def setup() -> None:
    for who, cookie in (("A", A), ("B", B)):
        status, body = api(cookie, "PUT", "/api/settings", {"hitl_enabled": False})
        if status != 200:
            fail(f"{who}: PUT settings: HTTP {status} {body!r}")
        status, body = api(cookie, "GET", "/api/settings")
        if status != 200 or body.get("hitl_enabled") is not False:
            fail(f"{who}: hitl_enabled not off: HTTP {status} {body!r}")
    status, body = api(A, "POST", "/api/platform/files/write", {"path": PRIVATE, "content": "private to A"})
    if status != 200:
        fail(f"A: plant {PRIVATE}: HTTP {status} {body!r}")
    if listing(B, "/personal"):
        fail(f"B's /personal is not empty: {listing(B, '/personal')}")
    status, _ = api(B, "GET", f"/api/platform/files/stat?path={q(PRIVATE)}")
    if status != 404:
        fail(f"B stat {PRIVATE}: HTTP {status}, expected 404 (B's own personal space)")
    print(f"OK: HITL off for A and B; A planted {PRIVATE}; B's /personal is empty")


async def owner_writes() -> None:
    prompt = (
        f"Use the write_file tool to create {NOTE} containing exactly this text and nothing "
        f"else: {MARKER}\nThen reply with the single word DONE."
    )
    for attempt in (1, 2):
        await turn(f"A attempt {attempt}/2", A, prompt)
        status, text = content(A, NOTE)
        if status == 200 and text == MARKER:
            break
        print(f"  WARN: {NOTE} is HTTP {status} {text!r} after attempt {attempt}")
        api(A, "DELETE", f"/api/platform/files?path={q(NOTE)}")
        if attempt == 2:
            fail(f"A's agent never wrote {NOTE} with the marker")
    space_untouched("after A's write")
    print(f"OK: A's agent wrote {NOTE}; A and B both read {MARKER!r} through the files API")


async def viewer_write_refused() -> None:
    target = f"{SHARED}/viewer-attempt.md"
    prompt = (
        f"Use the write_file tool to create {target} containing the word hello. "
        "Call write_file exactly once, do not use any other tool, and then report exactly "
        "what the tool returned."
    )
    for attempt in (1, 2):
        _, frames = await turn(f"B write attempt {attempt}/2", B, prompt)
        space_untouched(f"after B's write attempt {attempt}")
        into_space = [a for a in tool_starts(frames, "write_file") if SHARED in json.dumps(a)]
        results = tool_results(frames, "write_file")
        if into_space and results and all("Permission denied" in r for r in results):
            print(f"OK: B's write_file into {SHARED} refused: {results[0]!r}; nothing written")
            return
        print(f"  WARN: attempt {attempt}: write_file calls into the space {into_space}, results {results}")
    fail(f"B's agent never made a refused write_file call into {SHARED}")


def exec_binds(thread_id: str) -> list:
    out = subprocess.run(
        ["docker", "inspect", f"homeai-exec-{thread_id}"], capture_output=True, text=True, check=False
    )
    if out.returncode != 0:
        fail(f"docker inspect homeai-exec-{thread_id}: {out.stderr.strip()}")
    mounts = json.loads(out.stdout)[0]["Mounts"]
    return sorted((m["Destination"], m["RW"]) for m in mounts if m["Type"] == "bind")


async def viewer_exec_read_only() -> None:
    d = f"/files/spaces/{SPACE}"
    command = (
        f"cat {d}/{NOTE_NAME}; echo; touch {d}/exec-attempt.txt; echo touch_rc=$?; "
        f"echo appended >> {d}/{NOTE_NAME}; echo append_rc=$?"
    )
    prompt = (
        f"Use your execute_code tool to run exactly this command: bash -lc '{command}'. "
        "Run it once, exactly as given, and then report its output."
    )
    for attempt in (1, 2):
        thread_id, frames = await turn(f"B exec attempt {attempt}/2", B, prompt)
        space_untouched(f"after B's execute_code attempt {attempt}")
        results = tool_results(frames, "execute_code")
        seen = [r for r in results if MARKER in r and "Read-only file system" in r]
        if seen and not any(s in r for r in results for s in ("touch_rc=0", "append_rc=0")):
            binds = exec_binds(thread_id)
            if (d, False) not in binds or ("/files/personal", True) not in binds:
                fail(f"B's exec container binds {binds}, expected {d} read-only and /files/personal writable")
            print(f"OK: B's execute_code read the note, writes failed read-only; binds {binds}")
            return
        print(f"  WARN: attempt {attempt}: execute_code results {results}")
    fail("B's agent never ran the execute_code command that shows the space read-only")


async def viewer_refused() -> None:
    await viewer_write_refused()
    await viewer_exec_read_only()


phase = sys.argv[1]
if phase == "setup":
    setup()
elif phase == "owner-writes":
    asyncio.run(owner_writes())
elif phase == "viewer-refused":
    asyncio.run(viewer_refused())
else:
    fail(f"unknown phase {phase}")
PY
)"

turns() {
  timeout 1200 uvx --quiet --from websockets python3 -c "$TURNS_PY" "$1"
}

log "Step 1/4: HITL off, A plants a private file, A's agent writes ${NOTE_NAME} into the space..."
turns setup
turns owner-writes

log "Step 2/4: B (viewer) opens the space in the Files UI (Playwright)..."
cd "$SCRIPT_DIR"
if [ ! -d node_modules/playwright ]; then
  npm install
fi
npx playwright install chromium >/dev/null
G11_VIEWER="$USER_B" G11_VIEWER_PASSWORD="$PASSWORD_B" G11_SPACE_NAME="$SPACE_NAME" \
  node "$SCRIPT_DIR/shared_space_viewer_browser.mjs"
cd "$REPO_ROOT"

log "Steps 3-4/4: B's agent: write_file into the space refused; execute_code sees it read-only..."
turns viewer-refused

echo "SHARED SPACE VIEWER SMOKE: PASS"

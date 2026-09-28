#!/usr/bin/env bash
# M11-02 acceptance: the agent's file tools act as the signed-in user, through
# Caddy, with the real model, against the live stack. Three throwaway `e2e-*`
# users (A, B, C via `lib/auth.sh`) and a shared space `e2e-dlg-<hex>` that A
# owns, B edits and C views (recovery CLI).
#
#   1. A's agent writes `/personal/notes.md`; A's Files API (what the Files
#      tab lists) shows it with the exact content.
#   2. B's agent reads `/personal/notes.md`: not found (B's own, empty
#      personal space); A's marker appears nowhere in B's turn.
#   3. A's agent writes `/spaces/<slug>/plan.md`; B's agent (editor) reads it
#      and edits it; A sees B's edit.
#   4. C's agent (viewer) reads the edited file, and its write into the space
#      is refused ("Permission denied"); nothing was created.
#
# Each turn's transcript (prompt, tool calls with their results, reply) is
# printed. HITL is turned off for these users so the tools run directly.
# Everything created (users, personal spaces, the shared space, threads) is
# deleted on exit. Never completes bootstrap.
#
# Needs the live stack with the real model: NOT runnable offline.
#
# Usage: scripts/e2e/agent_tenancy_smoke.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$REPO_ROOT"
# shellcheck source=lib/auth.sh
source "$SCRIPT_DIR/lib/auth.sh"

SPACE="e2e-dlg-$(openssl rand -hex 3)"

cleanup() {
  local ids
  ids="$(_e2e_psql homeai_platform "SELECT id FROM spaces WHERE slug = '$SPACE'" 2>/dev/null || true)"
  _e2e_psql homeai_platform "DELETE FROM spaces WHERE slug = '$SPACE'" >/dev/null 2>&1 || true
  for id in $ids; do
    [[ "$id" =~ ^[0-9a-f-]{36}$ ]] && _e2e_compose exec -T platform rm -rf "/data/spaces/$id" || true
  done
  e2e_auth_end
}
trap cleanup EXIT

cli() { docker compose exec -T platform python -m app.cli "$@" >/dev/null; }

e2e_auth_create_user dlg-a
USER_A="$E2E_NEW_USER"
COOKIE_A="$(e2e_auth_login "$USER_A" "$E2E_NEW_PASSWORD")"
e2e_auth_create_user dlg-b
USER_B="$E2E_NEW_USER"
COOKIE_B="$(e2e_auth_login "$USER_B" "$E2E_NEW_PASSWORD")"
e2e_auth_create_user dlg-c
USER_C="$E2E_NEW_USER"
COOKIE_C="$(e2e_auth_login "$USER_C" "$E2E_NEW_PASSWORD")"

cli create-space "$SPACE" --name "E2E delegation" --owner "$USER_A"
cli add-member "$SPACE" "$USER_B" --role editor
cli add-member "$SPACE" "$USER_C" --role viewer
echo "users: A=$USER_A B=$USER_B (editor) C=$USER_C (viewer); space: /spaces/$SPACE"

COOKIE_A="$COOKIE_A" COOKIE_B="$COOKIE_B" COOKIE_C="$COOKIE_C" SPACE="$SPACE" \
  timeout 900 uvx --quiet --from websockets python3 - <<'PY'
import asyncio
import json
import os
import secrets
import sys
import urllib.error
import urllib.parse
import urllib.request

from websockets.asyncio.client import connect

BASE = os.environ.get("E2E_BASE", "http://localhost").rstrip("/")
SPACE = os.environ["SPACE"]
A, B, C = (os.environ[f"COOKIE_{u}"] for u in "ABC")
TURN_TIMEOUT_S = 240


def fail(msg: str) -> None:
    print(f"FAIL: {msg}", file=sys.stderr)
    sys.exit(1)


def api(cookie: str, method: str, path: str, body=None, raw: bool = False):
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


def listing(cookie: str, path: str) -> list[str]:
    status, body = api(cookie, "GET", f"/api/platform/files?path={urllib.parse.quote(path)}")
    if status != 200:
        fail(f"list {path}: HTTP {status} {body!r}")
    return [e["name"] for e in body["entries"]]


def content(cookie: str, path: str) -> tuple[int, bytes]:
    q = urllib.parse.quote(path)
    return api(cookie, "GET", f"/api/platform/files/download?path={q}", raw=True)


async def turn(who: str, cookie: str, prompt: str) -> list[dict]:
    status, thread = api(cookie, "POST", "/api/threads", {})
    if status != 201:
        fail(f"{who}: create thread: HTTP {status} {thread!r}")
    ws_base = BASE.replace("http", "ws", 1)
    frames = []
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
    print(f"\n--- {who}: {prompt}")
    for f in frames:
        if f["type"] == "tool_start":
            print(f"  tool_start {f['name']} {json.dumps(f['args'])}")
        elif f["type"] == "tool_end":
            print(f"  tool_end   {f['name']} [{f['status']}] {f['result_preview'][:300]!r}")
    reply = "".join(f["content"] for f in frames if f["type"] == "token")
    print(f"  reply: {reply.strip()[:400]!r}")
    end = frames[-1] if frames else {}
    if end.get("type") != "turn_end" or end.get("status") != "completed":
        fail(f"{who}: turn did not complete: {end}")
    return frames


def tool_results(frames: list[dict], name: str) -> list[str]:
    return [f["result_preview"] for f in frames if f["type"] == "tool_end" and f["name"] == name]


async def main() -> None:
    for cookie in (A, B, C):
        status, _ = api(cookie, "PUT", "/api/settings", {"hitl_enabled": False})
        if status != 200:
            fail(f"settings: HTTP {status}")

    marker_a = f"alpha-{secrets.token_hex(4)}"
    marker_s = f"shared-{secrets.token_hex(4)}"
    marker_s2 = f"edited-{secrets.token_hex(4)}"
    plan = f"/spaces/{SPACE}/plan.md"

    print("\n== 1. A writes /personal/notes.md")
    frames = await turn(
        "A",
        A,
        "Use the write_file tool to create /personal/notes.md containing exactly this "
        f"text and nothing else: {marker_a}\nThen reply with the single word DONE.",
    )
    if not any("Updated file /personal/notes.md" in r for r in tool_results(frames, "write_file")):
        fail(f"A: no successful write_file to /personal/notes.md: {tool_results(frames, 'write_file')}")
    if "notes.md" not in listing(A, "/personal"):
        fail("A: notes.md not in A's /personal listing")
    status, data = content(A, "/personal/notes.md")
    if status != 200 or data.decode().strip() != marker_a:
        fail(f"A: /personal/notes.md content: HTTP {status} {data!r}")
    print(f"  A's Files: /personal lists notes.md; content {data.decode().strip()!r}")

    print("\n== 2. B reads /personal/notes.md (B's own personal space)")
    frames = await turn(
        "B",
        B,
        "Use the read_file tool to read /personal/notes.md and tell me exactly what it says. "
        "If it doesn't exist, say NOT FOUND.",
    )
    reads = tool_results(frames, "read_file")
    if not reads or not all("not found" in r for r in reads):
        fail(f"B: read_file of /personal/notes.md should be not found: {reads}")
    if marker_a in json.dumps(frames):
        fail("B: A's marker leaked into B's turn")
    if listing(B, "/personal"):
        fail(f"B: /personal not empty: {listing(B, '/personal')}")
    print("  B's Files: /personal is empty")

    print(f"\n== 3. A writes {plan}; B (editor) reads and edits it")
    frames = await turn(
        "A",
        A,
        f"Use the write_file tool to create {plan} containing exactly this text and nothing "
        f"else: {marker_s}\nThen reply with the single word DONE.",
    )
    status, data = content(A, plan)
    if status != 200 or data.decode().strip() != marker_s:
        fail(f"A: {plan} content: HTTP {status} {data!r}")
    frames = await turn(
        "B",
        B,
        f"Use read_file to read {plan}. Then use edit_file on {plan} to replace the text "
        f"{marker_s} with {marker_s2}. Reply with the single word DONE.",
    )
    if not any(marker_s in r for r in tool_results(frames, "read_file")):
        fail(f"B: read_file of {plan} didn't return A's text: {tool_results(frames, 'read_file')}")
    status, data = content(A, plan)
    if status != 200 or data.decode().strip() != marker_s2:
        fail(f"A doesn't see B's edit: HTTP {status} {data!r}")
    print(f"  A's Files: {plan} now {data.decode().strip()!r}")

    print(f"\n== 4. C (viewer) reads {plan}, then tries to write into the space")
    viewer_file = f"/spaces/{SPACE}/viewer.md"
    frames = await turn(
        "C",
        C,
        f"First use read_file to read {plan} and tell me what it says. Then use write_file "
        f"to create {viewer_file} containing the word hello. Report exactly what happened.",
    )
    if not any(marker_s2 in r for r in tool_results(frames, "read_file")):
        fail(f"C: read_file of {plan}: {tool_results(frames, 'read_file')}")
    writes = tool_results(frames, "write_file")
    if not writes or not all("Permission denied" in r for r in writes):
        fail(f"C: write_file should be refused: {writes}")
    status, _ = content(A, viewer_file)
    if status != 404:
        fail(f"C's write went through: {viewer_file} HTTP {status}")
    print(f"  A's Files: {viewer_file} does not exist")

    print("\nPASS: agent file tools act as their user (personal isolation, editor, viewer)")


asyncio.run(main())
PY

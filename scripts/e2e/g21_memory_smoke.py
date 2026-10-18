#!/usr/bin/env python3
"""M21-12: the G21 memory scenario. See g21_memory_smoke.sh.

Env: G21_COOKIE, G21_OTHER_COOKIE, E2E_BASE (default http://localhost).
"""

from __future__ import annotations

import asyncio
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from websockets.asyncio.client import connect

BASE = os.environ.get("E2E_BASE", "http://localhost").rstrip("/")
MEMORY = "/personal/Memory"
TURN_TIMEOUT_S = 600
RUN_BUDGET_S = 1200
INDEX_BUDGET_S = 300
TERMINAL = {"succeeded", "failed", "timed_out", "missed", "waiting_approval", "expired"}
OFF_TOOLS = {
    "web_search",
    "web_fetch",
    "open_integration",
    "use_integration",
    "read_integration_resource",
    "get_integration_prompt",
}
FACT = "My sister Ingrid keeps bees on her farm outside Petaluma. Suggest a name for her honey, in one line."
QUERY = "Ingrid bees Petaluma"


def log(msg: str) -> None:
    print(f"[g21] {time.strftime('%H:%M:%S')} {msg}", flush=True)


def fail(msg: str) -> None:
    log(f"FAIL: {msg}")
    raise SystemExit(1)


class User:
    def __init__(self, cookie: str) -> None:
        self.cookie = cookie

    def call(self, method: str, path: str, body: Any = None) -> tuple[int, Any]:
        req = urllib.request.Request(
            f"{BASE}{path}",
            data=None if body is None else json.dumps(body).encode(),
            method=method,
            headers={"Cookie": self.cookie, "Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                status, raw = resp.status, resp.read()
        except urllib.error.HTTPError as e:
            status, raw = e.code, e.read()
        try:
            return status, json.loads(raw) if raw else None
        except ValueError:
            return status, raw.decode(errors="replace")

    def api(self, method: str, path: str, body: Any = None, want: int = 200) -> Any:
        status, out = self.call(method, path, body)
        if status != want:
            fail(f"{method} {path}: HTTP {status} {str(out)[:300]}")
        return out

    def files_under(self, path: str) -> list[str]:
        status, listing = self.call("GET", f"/api/platform/files?path={urllib.parse.quote(path)}")
        if status != 200:
            return []
        out: list[str] = []
        for entry in listing["entries"]:
            out += self.files_under(entry["path"]) if entry["type"] == "dir" else [entry["path"]]
        return out

    def search(self, query: str) -> list[dict]:
        return self.api("POST", "/api/platform/files/search", {"query": query, "limit": 10})["results"]

    async def chat(self, content: str) -> dict:
        thread = self.api("POST", "/api/threads", {}, want=201)["id"]
        tools: list[str] = []
        reply: list[str] = []
        ws_base = BASE.replace("http", "ws", 1)
        async with connect(f"{ws_base}/ws/chat/{thread}", additional_headers={"Cookie": self.cookie}) as ws:
            await ws.send(json.dumps({"type": "user_message", "content": content}))
            async with asyncio.timeout(TURN_TIMEOUT_S):
                async for raw in ws:
                    frame = json.loads(raw)
                    if frame["type"] == "tool_start":
                        tools.append(frame["name"])
                    if frame["type"] == "token":
                        reply.append(frame.get("content") or "")
                    if frame["type"] == "approval_request":
                        fail(f"unexpected approval card: {[a['name'] for a in frame['actions']]}")
                    if frame["type"] == "error":
                        fail(f"turn error: {frame!r}")
                    if frame["type"] == "turn_end":
                        break
        return {"tools": tools, "reply": "".join(reply)}


def main() -> None:
    me = User(os.environ["G21_COOKIE"])
    other = User(os.environ["G21_OTHER_COOKIE"])
    for user in (me, other):
        user.api("PUT", "/api/settings", {"timezone": "America/Los_Angeles"})
    me.api("PATCH", "/api/platform/tools", {"changes": [{"name": n, "permission": "allow"} for n in ("write_file", "edit_file", "delete")]})

    memory = [r for r in me.api("GET", "/api/routines") if r.get("delivered") and r["name"] == "Memory"]
    if not memory:
        fail("the Memory routine wasn't delivered")
    routine = memory[0]
    status, body = me.call("DELETE", f"/api/routines/{routine['id']}")
    if status != 409 or "delivered_routine" not in json.dumps(body):
        fail(f"deleting the Memory routine should be 409 delivered_routine, got {status} {body!r}")
    log("OK: deleting the Memory routine is refused (409 delivered_routine)")

    told = asyncio.run(me.chat(FACT))
    log(f"chat: {told['reply'].strip()[:100]!r}")
    status, readme = me.call("GET", f"/api/platform/files/download?path={urllib.parse.quote(MEMORY + '/README.md')}")
    if status != 200 or "one file per subject" not in str(readme):
        fail(f"no conventions README after the first chat: HTTP {status} {str(readme)[:200]!r}")
    log("OK: /personal/Memory/README.md holds the conventions")

    run = me.api("POST", f"/api/routines/{routine['id']}/run", want=202)
    deadline = time.monotonic() + RUN_BUDGET_S
    while True:
        status = next(r for r in me.api("GET", f"/api/routines/{routine['id']}/runs") if r["id"] == run["id"])
        if status["status"] in TERMINAL:
            break
        if time.monotonic() > deadline:
            fail(f"the Memory run didn't finish within {RUN_BUDGET_S}s")
        time.sleep(5)
    messages = me.api("GET", f"/api/threads/{run['thread_id']}/messages")
    if status["status"] != "succeeded":
        last = [c for m in messages[-3:] if m["role"] == "assistant" for c in (m.get("tool_calls") or [])]
        fail(f"the Memory run ended {status['status']}: {status.get('detail')}; last calls {last!r}")
    used = {c["name"] for m in messages if m["role"] == "assistant" for c in (m.get("tool_calls") or [])}
    if used & OFF_TOOLS:
        fail(f"the Memory run used {sorted(used & OFF_TOOLS)}")
    notes = {p: me.api("GET", f"/api/platform/files/download?path={urllib.parse.quote(p)}") for p in me.files_under(MEMORY)}
    holding = [p for p, text in notes.items() if "ingrid" in str(text).lower()]
    if not holding:
        fail(f"the fact isn't in Memory: {notes!r}")
    log(f"OK: Memory run put it in {holding} (tools {sorted(used)}, none of the web or integration tools)")

    recall = asyncio.run(me.chat("What does my sister do? One line."))
    if "bee" not in recall["reply"].lower() and "honey" not in recall["reply"].lower():
        fail(f"a new chat didn't recall it: tools {recall['tools']}, reply {recall['reply']!r}")
    log(f"OK: a new chat recalls it (tools {recall['tools']}): {recall['reply'].strip()!r}")

    deadline = time.monotonic() + INDEX_BUDGET_S
    while not any(r["path"] in holding for r in me.search(QUERY)):
        if time.monotonic() > deadline:
            fail(f"the owner's search never found {holding} within {INDEX_BUDGET_S}s")
        time.sleep(3)
    log("OK: the page is indexed and the owner's search finds it")

    if other.files_under(MEMORY):
        fail(f"the other user sees notes under {MEMORY}: {other.files_under(MEMORY)}")
    leaked = [r for r in other.search(QUERY) if "ingrid" in r.get("snippet", "").lower()]
    if leaked:
        fail(f"the other user's search finds the first user's notes: {leaked!r}")
    found = asyncio.run(other.chat("Use search_files to look for Ingrid and her bees in my files. One line."))
    if "search_files" not in found["tools"]:
        fail(f"the other user's agent didn't call search_files: {found['tools']}")
    if "petaluma" in found["reply"].lower():
        fail(f"the other user's agent found the first user's notes: {found['reply']!r}")
    log(f"OK: the other user can't see or find it (agent: {found['reply'].strip()[:100]!r})")
    log("PASS")


if __name__ == "__main__":
    main()

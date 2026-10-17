#!/usr/bin/env python3
"""M21-04: file search on the live stack. See m21_search_smoke.sh.

Env: E2E_AUTH_COOKIE, E2E_BASE (default http://localhost).
"""

from __future__ import annotations

import asyncio
import json
import os
import time
import urllib.error
import urllib.request
from typing import Any

from websockets.asyncio.client import connect

BASE = os.environ.get("E2E_BASE", "http://localhost").rstrip("/")
COOKIE = os.environ.get("E2E_AUTH_COOKIE", "")
INDEX_BUDGET_S = 180
TURN_TIMEOUT_S = 600

NOTE = "/personal/notes/family.md"
FILES = {
    NOTE: (
        "# Family\n\n"
        "My sister Ingrid keeps bees on her farm outside Petaluma. Every August she\n"
        "brings us a few jars of honey from her hives.\n"
    ),
    "/personal/notes/car.md": "# Car\n\nThe Subaru needs new brake pads before winter.\n",
    "/personal/notes/recipes.md": "# Recipes\n\nPancakes: two eggs, a cup of flour, milk.\n",
}
PARAPHRASE = "which relative of mine is an apiarist"
# The other M21 smokes import these helpers and set their own.
PREFIX = "m21-search"


def log(msg: str) -> None:
    print(f"[{PREFIX}] {time.strftime('%H:%M:%S')} {msg}", flush=True)


def fail(msg: str) -> None:
    log(f"FAIL: {msg}")
    raise SystemExit(1)


def api(method: str, path: str, body: Any = None, want: int = 200) -> Any:
    req = urllib.request.Request(
        f"{BASE}{path}",
        data=None if body is None else json.dumps(body).encode(),
        method=method,
        headers={"Cookie": COOKIE, "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            status, raw = resp.status, resp.read()
    except urllib.error.HTTPError as e:
        status, raw = e.code, e.read()
    if status != want:
        fail(f"{method} {path}: HTTP {status} {raw[:300]!r}")
    return json.loads(raw) if raw else None


def search(query: str) -> dict:
    return api("POST", "/api/platform/files/search", {"query": query, "limit": 3})


def wait_until_found(query: str) -> dict:
    deadline = time.monotonic() + INDEX_BUDGET_S
    while True:
        found = search(query)
        results = found["results"]
        if found["semantic"] and results and results[0]["path"] == NOTE:
            return found
        if time.monotonic() > deadline:
            fail(f"{query!r} didn't find {NOTE} first within {INDEX_BUDGET_S}s: {found!r}")
        time.sleep(3)


async def turn(thread_id: str, content: str) -> dict:
    frames: list[dict] = []
    ws_base = BASE.replace("http", "ws", 1)
    async with connect(f"{ws_base}/ws/chat/{thread_id}", additional_headers={"Cookie": COOKIE}) as ws:
        await ws.send(json.dumps({"type": "user_message", "content": content}))
        async with asyncio.timeout(TURN_TIMEOUT_S):
            async for raw in ws:
                frame = json.loads(raw)
                frames.append(frame)
                if frame["type"] == "approval_request":
                    fail(f"search is read-only, but a card came: {frame['actions']!r}")
                if frame["type"] == "error":
                    fail(f"turn error: {frame!r}")
                if frame["type"] == "turn_end":
                    break
    tools = [f for f in frames if f["type"] == "tool_end"]
    reply = "".join(f.get("content") or "" for f in frames if f["type"] == "token")
    return {"tools": tools, "reply": reply}


def main() -> None:
    for path, content in FILES.items():
        api("POST", "/api/platform/files/write", {"path": path, "content": content})
    log(f"wrote {len(FILES)} notes")

    started = time.monotonic()
    found = wait_until_found(PARAPHRASE)
    log(f"OK: a paraphrase finds the note by meaning ({time.monotonic() - started:.0f}s to index)")
    hit = found["results"][0]
    if (hit["line_start"], hit["line_end"]) != (1, 4) or "Ingrid" not in hit["snippet"]:
        fail(f"unexpected hit: {hit!r}")

    found = search("Petaluma")
    if not found["results"] or found["results"][0]["path"] != NOTE:
        fail(f"an exact place name should find the note first: {found!r}")
    log("OK: an exact name finds it too")

    thread = api("POST", "/api/threads", {}, want=201)["id"]
    result = asyncio.run(
        turn(thread, "Search my files: which relative of mine is an apiarist? Answer in one line.")
    )
    names = [t["name"] for t in result["tools"]]
    if "search_files" not in names:
        fail(f"the agent should use search_files: tools {names}, reply {result['reply']!r}")
    if "Ingrid" not in result["reply"]:
        fail(f"the reply should name Ingrid: tools {names}, reply {result['reply']!r}")
    log(f"OK: the agent found it with search_files (tools {names}): {result['reply'].strip()!r}")
    api("DELETE", f"/api/threads/{thread}", want=204)


if __name__ == "__main__":
    main()

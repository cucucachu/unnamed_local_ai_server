#!/usr/bin/env python3
"""M21-10: the Memory routine on the live stack. See m21_memory_routine_smoke.sh.

Env: E2E_AUTH_COOKIE, E2E_BASE (default http://localhost).
"""

from __future__ import annotations

import asyncio
import re
import time
import urllib.parse
import urllib.request

import m21_search_smoke
from m21_search_smoke import BASE, COOKIE, api, fail, log, turn

m21_search_smoke.PREFIX = "m21-routine"

MEMORY = "/personal/Memory"
RUN_BUDGET_S = 900
TERMINAL = {"succeeded", "failed", "timed_out", "missed", "waiting_approval", "expired"}
MONTH = re.compile(r"\b(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]* 20\d\d\b")
SEEDS = (
    "My partner Priya and I just booked a trip to Lisbon for March. "
    "Suggest two neighbourhoods to stay in, in one line.",
    "I grew up in Duluth, Minnesota, and I miss Lake Superior. "
    "Name one lake in Norway that feels similar, in one line.",
)


def download(path: str) -> str:
    req = urllib.request.Request(
        f"{BASE}/api/platform/files/download?path={urllib.parse.quote(path)}",
        headers={"Cookie": COOKIE},
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        return resp.read().decode()


def files_under(path: str) -> list[str]:
    out: list[str] = []
    for entry in api("GET", f"/api/platform/files?path={urllib.parse.quote(path)}")["entries"]:
        if entry["type"] == "dir":
            out += files_under(entry["path"])
        else:
            out.append(entry["path"])
    return out


def main() -> None:
    api("PUT", "/api/settings", {"timezone": "America/Los_Angeles"})
    names = ("write_file", "edit_file")
    api("PATCH", "/api/platform/tools", {"changes": [{"name": n, "permission": "allow"} for n in names]})
    memory = [r for r in api("GET", "/api/routines") if r.get("delivered") and r["name"] == "Memory"]
    if not memory:
        fail("the Memory routine wasn't delivered after the timezone was set")
    routine = memory[0]
    log(f"OK: Memory delivered ({routine['schedule']}, can't use {[t['name'] for t in routine['unavailable_tools']]})")

    for seed in SEEDS:
        thread = api("POST", "/api/threads", {}, want=201)["id"]
        result = asyncio.run(turn(thread, seed))
        log(f"seeded a chat: {result['reply'].strip()[:80]!r}")

    run = api("POST", f"/api/routines/{routine['id']}/run", want=202)
    log(f"Run now: run {run['id']}, chat {run['thread_id']}")
    deadline = time.monotonic() + RUN_BUDGET_S
    while True:
        status = next(r for r in api("GET", f"/api/routines/{routine['id']}/runs") if r["id"] == run["id"])
        if status["status"] in TERMINAL:
            break
        if time.monotonic() > deadline:
            fail(f"the run didn't finish within {RUN_BUDGET_S}s: {status!r}")
        time.sleep(5)
    messages = api("GET", f"/api/threads/{run['thread_id']}/messages")
    reply = next((m["content"] for m in reversed(messages) if m["role"] == "assistant" and m["content"]), "")
    if status["status"] != "succeeded":
        fail(f"the run ended {status['status']} ({status['detail']}): {reply[:400]!r}")
    log(f"OK: the run succeeded; reply {reply.strip()[:600]!r}")

    paths = files_under(MEMORY)
    log(f"files: {paths}")
    if f"{MEMORY}/memory.md" not in paths:
        fail("no memory.md")
    if len(paths) < 2:
        fail("no pages besides memory.md")
    texts = {p: download(p) for p in paths}
    everything = "\n".join(texts.values()).lower()
    for fact in ("priya", "duluth"):
        if fact not in everything:
            fail(f"{fact!r} isn't in the notes: {texts!r}")
    if not MONTH.search("\n".join(texts.values())):
        fail(f"no fact carries its month: {texts!r}")
    if "file:" not in reply:
        fail(f"the reply should link what changed: {reply!r}")
    for path, text in texts.items():
        log(f"--- {path}\n{text.strip()}")
    log("PASS")


if __name__ == "__main__":
    main()

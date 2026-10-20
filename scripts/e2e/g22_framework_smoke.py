#!/usr/bin/env python3
"""M22-20: the G22 app framework scenario. See g22_framework_smoke.sh.

Env: G22_COOKIE, G22_OTHER_COOKIE, G22_STATE, E2E_BASE (default http://localhost).
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from websockets.asyncio.client import connect

BASE = os.environ.get("E2E_BASE", "http://localhost").rstrip("/")
STATE = os.environ["G22_STATE"]
TIMEZONE = "America/Los_Angeles"
MEMORY = "/personal/Memory"
CORE = f"{MEMORY}/memory.md"
TIMELINE = f"{MEMORY}/timeline"
TURN_TIMEOUT_S = 600
RUN_BUDGET_S = 1200
INDEX_BUDGET_S = 300
TERMINAL = {"succeeded", "failed", "timed_out", "missed", "waiting_approval", "expired"}
WEB_TOOLS = {
    "web_search",
    "web_fetch",
    "open_integration",
    "use_integration",
    "read_integration_resource",
    "get_integration_prompt",
}
# Made up, so neither the model nor the web knows it.
TITLE = "The Hollow Lighthouse"
BOOK = {
    "title": TITLE,
    "creator": "Marisol Quennell",
    "notes": "Lent to me by my neighbour Tobias. A keeper alone on a storm island who counts the gulls every dawn.",
}
PARAPHRASE = "novel about a man tending a beacon on a stormy island who tallies seabirds"


def log(msg: str) -> None:
    print(f"[g22] {time.strftime('%H:%M:%S')} {msg}", flush=True)


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

    def read(self, path: str) -> str | None:
        status, out = self.call(
            "GET", f"/api/platform/files/download?path={urllib.parse.quote(path)}"
        )
        return str(out) if status == 200 else None

    def write(self, path: str, content: str) -> None:
        self.api("POST", "/api/platform/files/write", {"path": path, "content": content})

    def files_under(self, path: str) -> list[str]:
        status, listing = self.call("GET", f"/api/platform/files?path={urllib.parse.quote(path)}")
        if status != 200:
            return []
        out: list[str] = []
        for entry in listing["entries"]:
            out += self.files_under(entry["path"]) if entry["type"] == "dir" else [entry["path"]]
        return out

    def search(self, query: str) -> list[dict]:
        return self.api("POST", "/api/platform/files/search", {"query": query, "limit": 10})[
            "results"
        ]

    async def chat(self, content: str) -> dict:
        thread = self.api("POST", "/api/threads", {}, want=201)["id"]
        tools: list[str] = []
        reply: list[str] = []
        ws_base = BASE.replace("http", "ws", 1)
        async with connect(
            f"{ws_base}/ws/chat/{thread}", additional_headers={"Cookie": self.cookie}
        ) as ws:
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

    def run(self, routine: dict) -> dict:
        """Run now and wait; the run's status, tools and reply."""
        run = self.api("POST", f"/api/routines/{routine['id']}/run", want=202)
        deadline = time.monotonic() + RUN_BUDGET_S
        while True:
            status = next(
                r
                for r in self.api("GET", f"/api/routines/{routine['id']}/runs")
                if r["id"] == run["id"]
            )
            if status["status"] in TERMINAL:
                break
            if time.monotonic() > deadline:
                fail(f"{routine['name']} didn't finish within {RUN_BUDGET_S}s")
            time.sleep(5)
        messages = self.api("GET", f"/api/threads/{run['thread_id']}/messages")
        tools = {
            c["name"]
            for m in messages
            if m["role"] == "assistant"
            for c in (m.get("tool_calls") or [])
        }
        results = [
            str(m.get("content"))[:600]
            for m in messages
            if m["role"] == "tool" and m.get("tool_name") == "list_app_changes"
        ]
        reply = next(
            (m["content"] for m in reversed(messages) if m["role"] == "assistant" and m["content"]),
            "",
        )
        if status["status"] != "succeeded":
            fail(
                f"{routine['name']} ended {status['status']}: {status.get('detail')}; tools {sorted(tools)}"
            )
        if tools & WEB_TOOLS:
            fail(f"{routine['name']} used {sorted(tools & WEB_TOOLS)}")
        return {"tools": tools, "reply": reply, "app_changes": results}


def install_books(me: User) -> dict:
    space = next(
        s for s in me.api("GET", "/api/platform/spaces")["spaces"] if s["kind"] == "personal"
    )
    catalog = [t["slug"] for t in me.api("GET", "/api/platform/app-templates")["templates"]]
    if "books" not in catalog:
        fail(f"the catalog has no books template: {catalog}")
    out = me.api(
        "POST",
        f"/api/platform/spaces/{space['id']}/app-templates/books/install",
        {"slug": "books", "name": "Books and media"},
        want=201,
    )
    with open(STATE, "a") as f:
        f.write(f"{out['app']['id']}\n")
    if not (out["build"] and out["build"]["ok"]):
        fail(f"books didn't build: {out['build']!r}")
    return out["instance"]


def check_map(me: User, instance: dict) -> None:
    entry = next(
        (
            i
            for i in me.api("GET", "/api/platform/data-map")["instances"]
            if i["id"] == instance["id"]
        ),
        None,
    )
    if entry is None:
        fail("the data map doesn't list the installed Books app")
    items = next((t for t in entry["tables"] if t["name"] == "items"), None)
    if items is None or items["rows"] != 1 or not items["description"]:
        fail(f"the data map's items table: {items!r}")
    log(
        f"OK: the data map lists Books and media (items: {items['rows']} row, {items['description']!r})"
    )


def find_row(me: User, other: User) -> None:
    deadline = time.monotonic() + INDEX_BUDGET_S
    while not any(
        r["kind"] == "app_row" and r["app_row"]["title"] == TITLE for r in me.search(PARAPHRASE)
    ):
        if time.monotonic() > deadline:
            fail(
                f"the owner's search never found the row within {INDEX_BUDGET_S}s: {me.search(PARAPHRASE)!r}"
            )
        time.sleep(3)
    log("OK: the row is indexed and the owner's search finds it by meaning")

    for attempt in (1, 2):
        found = asyncio.run(
            me.chat(f"Use search_files to find the {PARAPHRASE} that's in my apps. One line.")
        )
        log(
            f"owner attempt {attempt}: tools {found['tools']}, reply {found['reply'].strip()[:120]!r}"
        )
        if "search_files" in found["tools"] and "hollow lighthouse" in found["reply"].lower():
            break
        if attempt == 2:
            fail("the owner's agent didn't find the row with search_files, twice")

    leaked = [r for r in other.search(PARAPHRASE) if TITLE in json.dumps(r)]
    if leaked:
        fail(f"the other user's search finds the row: {leaked!r}")
    found = asyncio.run(
        other.chat(f"Use search_files to find the {PARAPHRASE} in my files and apps. One line.")
    )
    if "search_files" not in found["tools"]:
        fail(f"the other user's agent didn't call search_files: {found['tools']}")
    if "hollow lighthouse" in found["reply"].lower() or "quennell" in found["reply"].lower():
        fail(f"the other user's agent found the row: {found['reply']!r}")
    log(f"OK: the other user can't find it (agent: {found['reply'].strip()[:100]!r})")


def memory_run(me: User, routines: dict[str, dict], today: str) -> None:
    day = f"{TIMELINE}/{today[:4]}/{today}.md"
    for attempt in (1, 2):
        out = me.run(routines["Memory"])
        if "list_app_changes" not in out["tools"]:
            fail(f"the Memory run didn't read the app changes: {sorted(out['tools'])}")
        text = me.read(day)
        if text is not None and "lighthouse" in text.lower():
            log(f"OK: Run now on Memory read the app row and wrote {day}")
            return
        log(
            f"attempt {attempt}: {day} doesn't mention the book ({text!r}); the run wrote "
            f"{me.files_under(MEMORY)}; list_app_changes gave {out['app_changes']!r}; reply {out['reply'][:300]!r}"
        )
        if attempt == 2:
            fail("the Memory run didn't put the book in today's day file, twice")


def week_files(today: datetime) -> tuple[dict[str, str], str]:
    """Two seeded day files in the last full week, and the week file to expect."""
    start = (today - timedelta(days=today.weekday() + 7)).date()
    year, week, _ = start.isocalendar()
    tue, thu = start + timedelta(days=1), start + timedelta(days=3)
    days = {
        f"{TIMELINE}/{tue.year}/{tue.isoformat()}.md": (
            f"# {tue:%A %B} {tue.day}, {tue.year}\n\n- Booked a trip to Bend with Ruth for the second weekend of next month.\n"
        ),
        f"{TIMELINE}/{thu.year}/{thu.isoformat()}.md": (
            f"# {thu:%A %B} {thu.day}, {thu.year}\n\n- Started a Thursday evening pottery class at Clayhouse.\n"
        ),
    }
    return days, f"{TIMELINE}/{year}/{year}-W{week:02d}.md"


def weekly_review(me: User, routines: dict[str, dict], today: datetime) -> None:
    days, week = week_files(today)
    for path, text in days.items():
        me.write(path, text)
    for attempt in (1, 2):
        me.run(routines["Memory: weekly review"])
        text, core = me.read(week), me.read(CORE) or ""
        lately = re.search(r"^## Lately\n(.*?)(?=^## |\Z)", core, re.M | re.S)
        missing = []
        if text is None or not ("bend" in text.lower() or "pottery" in text.lower()):
            missing.append(f"{week} (got {text!r})")
        if lately is None or week.rsplit("/", 1)[1] not in lately.group(1):
            missing.append(f"a ## Lately section linking it (memory.md: {core[:400]!r})")
        if not missing:
            log(f"OK: Run now on the weekly review wrote {week} and a ## Lately section")
            return
        log(f"attempt {attempt}: missing {missing}")
        if attempt == 2:
            fail("the weekly review didn't write the week and Lately, twice")


def main() -> None:
    me = User(os.environ["G22_COOKIE"])
    other = User(os.environ["G22_OTHER_COOKIE"])
    for user in (me, other):
        user.api("PUT", "/api/settings", {"timezone": TIMEZONE})
    me.api(
        "PATCH",
        "/api/platform/tools",
        {
            "changes": [
                {"name": n, "permission": "allow"}
                for n in ("write_file", "edit_file", "delete", "execute_code")
            ]
        },
    )
    routines = {r["name"]: r for r in me.api("GET", "/api/routines") if r.get("delivered")}
    for name in ("Memory", "Memory: weekly review"):
        if name not in routines:
            fail(f"the {name!r} routine wasn't delivered: {sorted(routines)}")
    now = datetime.now(ZoneInfo(TIMEZONE))
    today = now.date().isoformat()

    instance = install_books(me)
    me.api(
        "POST",
        f"/api/platform/apps/instances/{instance['id']}/rpc",
        {
            "op": "run",
            "sql": "INSERT INTO items (title, creator, kind, status, rating, started_on, finished_on, notes) "
            "VALUES (?, ?, 'book', 'finished', 5, ?, ?, ?)",
            "params": [BOOK["title"], BOOK["creator"], today, today, BOOK["notes"]],
        },
    )
    log(f"installed Books and media from the catalog and finished {TITLE!r} today")
    check_map(me, instance)
    find_row(me, other)
    memory_run(me, routines, today)
    weekly_review(me, routines, now)
    log("PASS")


if __name__ == "__main__":
    main()

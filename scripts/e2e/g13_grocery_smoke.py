#!/usr/bin/env python3
"""M13-05 GATE G13: drive the real local model through "make me a grocery
list app", then the runner UI, a data round-trip, and a revert.

Env: E2E_AUTH_COOKIE, G13_USER, G13_PASSWORD, G13_STATE (JSON file shared
with g13_grocery_smoke.mjs). Optional: E2E_BASE (default http://localhost),
G13_OUT (report dir, default this script's directory),
EVAL_TURN_TIMEOUT_S (default 1500).
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from websockets.asyncio.client import connect

HERE = Path(__file__).resolve().parent
BASE = os.environ.get("E2E_BASE", "http://localhost").rstrip("/")
COOKIE = os.environ.get("E2E_AUTH_COOKIE", "")
STATE_PATH = Path(os.environ["G13_STATE"])
OUT_DIR = Path(os.environ.get("G13_OUT", str(HERE)))
TURN_TIMEOUT_S = int(os.environ.get("EVAL_TURN_TIMEOUT_S", "1500"))
HEX = os.urandom(3).hex()
SLUG_WANT = "g13-grocery"
UI_ITEM = f"G13-Milk-{HEX}"
AGENT_ITEM = f"G13-Eggs-{HEX}"
QTY_COLS = ("quantity", "qty", "amount")

REPORT: dict[str, Any] = {
    "pass": False,
    "failure": None,
    "app_id": None,
    "instance_id": None,
    "slug": None,
    "template": None,
    "tools": [],
    "taxonomy": None,
}


def fail(msg: str, taxonomy: str | None = None) -> None:
    REPORT["failure"] = msg
    REPORT["taxonomy"] = taxonomy or msg.split(":")[0]
    write_report()
    print(f"FAIL: {msg}", file=sys.stderr)
    sys.exit(1)


def write_report() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "g13-last-report.json").write_text(
        json.dumps(REPORT, indent=2, default=str) + "\n"
    )


def log(msg: str) -> None:
    print(f"[g13] {msg}", flush=True)


def api(method: str, path: str, body: Any = None, timeout: int = 60) -> tuple[int, Any]:
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(
        f"{BASE}{path}",
        data=data,
        method=method,
        headers={"Cookie": COOKIE, "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            return resp.status, json.loads(raw) if raw else None
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            parsed = json.loads(raw) if raw else None
        except json.JSONDecodeError:
            parsed = raw.decode("utf-8", "replace")
        return e.code, parsed


def rpc(instance_id: str, payload: dict, timeout: int = 30) -> tuple[int, Any]:
    return api(
        "POST",
        f"/api/platform/apps/instances/{instance_id}/rpc",
        payload,
        timeout=timeout,
    )


def read_state() -> dict[str, Any]:
    if not STATE_PATH.exists():
        return {}
    try:
        return json.loads(STATE_PATH.read_text())
    except json.JSONDecodeError:
        return {}


def write_state(update: dict[str, Any]) -> None:
    state = read_state()
    state.update(update)
    tmp = STATE_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2) + "\n")
    tmp.replace(STATE_PATH)


async def turn(thread_id: str, prompt: str) -> list[dict]:
    ws_base = BASE.replace("http", "ws", 1)
    frames: list[dict] = []
    async with connect(
        f"{ws_base}/ws/chat/{thread_id}",
        additional_headers={"Cookie": COOKIE},
        max_size=2**22,
    ) as ws:
        await ws.send(json.dumps({"type": "user_message", "content": prompt}))
        async with asyncio.timeout(TURN_TIMEOUT_S):
            async for raw in ws:
                frame = json.loads(raw)
                frames.append(frame)
                if frame["type"] == "approval_request":
                    await ws.send(json.dumps({"type": "cancel"}))
                if frame["type"] in ("turn_end", "error"):
                    break
    return frames


def preview_frames(label: str, prompt: str, frames: list[dict]) -> None:
    print(f"\n=== {label} ===")
    print(f"prompt: {prompt[:220]}{'…' if len(prompt) > 220 else ''}")
    for frame in frames:
        kind = frame.get("type")
        if kind == "tool_start":
            print(f"  tool_start {frame.get('name')} {json.dumps(frame.get('args') or {}, default=str)[:240]}")
        elif kind == "tool_end":
            preview = (frame.get("result_preview") or "")[:240]
            print(f"  tool_end   {frame.get('name')} [{frame.get('status')}] {preview!r}")
        elif kind == "approval_request":
            print(f"  approval_request {json.dumps(frame.get('actions'), default=str)[:240]}")
        elif kind == "error":
            print(f"  error {frame.get('message')}")
        elif kind == "turn_end":
            print(f"  turn_end {frame.get('status')} {frame.get('duration_ms')} ms")
    reply = "".join(f.get("content") or "" for f in frames if f.get("type") == "token")
    if reply.strip():
        print(f"  reply: {reply.strip()[:400]!r}")


def record_tools(frames: list[dict]) -> None:
    for f in frames:
        if f.get("type") != "tool_end":
            continue
        args = next(
            (
                s.get("args")
                for s in frames
                if s.get("type") == "tool_start" and s.get("tool_call_id") == f.get("tool_call_id")
            ),
            None,
        )
        REPORT["tools"].append(
            {
                "name": f.get("name"),
                "status": f.get("status"),
                "args": args,
                "preview": (f.get("result_preview") or "")[:500],
            }
        )


def classify_turn(frames: list[dict]) -> str | None:
    if not frames:
        return "empty_turn"
    last = frames[-1]
    if last.get("type") == "error":
        return "turn_error"
    if last.get("type") != "turn_end":
        return "no_turn_end"
    if last.get("status") == "awaiting_approval":
        return "awaiting_approval"
    if last.get("status") == "cancelled":
        return "cancelled"
    if last.get("status") != "completed":
        return f"turn_{last.get('status')}"
    return None


def tool_previews(frames: list[dict], name: str) -> list[str]:
    return [
        (f.get("result_preview") or "")
        for f in frames
        if f.get("type") == "tool_end" and f.get("name") == name
    ]


def tool_starts(frames: list[dict], name: str) -> list[dict]:
    return [f.get("args") or {} for f in frames if f.get("type") == "tool_start" and f.get("name") == name]


def build_succeeded(frames: list[dict], app: dict | None) -> bool:
    for preview in tool_previews(frames, "build_app"):
        if preview.startswith("Build succeeded"):
            return True
    version = (app or {}).get("working_version") or {}
    return bool(version.get("bundle_path") or version.get("commit"))


def apps_in_personal() -> list[dict]:
    status, body = api("GET", "/api/platform/apps")
    if status != 200:
        fail(f"GET /apps: HTTP {status} {body!r}", "apps_api")
    return [a for a in body["apps"] if (a.get("source_path") or "").startswith("/personal/Apps/")]


def instance_for(app: dict) -> tuple[dict | None, dict]:
    status, spaces = api("GET", "/api/platform/spaces")
    if status != 200:
        fail(f"GET /spaces: HTTP {status} {spaces!r}", "spaces_api")
    personal = next(s for s in spaces["spaces"] if s["kind"] == "personal")
    status, body = api("GET", f"/api/platform/spaces/{personal['id']}/instances")
    if status != 200:
        fail(f"GET instances: HTTP {status} {body!r}", "instances_api")
    inst = next((i for i in body["instances"] if i["app_id"] == app["id"]), None)
    return inst, personal


def user_tables(instance_id: str) -> tuple[str | None, dict[str, list[dict]]]:
    status, body = rpc(
        instance_id,
        {
            "op": "getAll",
            "sql": "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'",
        },
    )
    if status != 200:
        return f"sqlite_master HTTP {status} {body!r}", {}
    tables: dict[str, list[dict]] = {}
    for row in body.get("rows") or []:
        name = row["name"]
        st, info = rpc(instance_id, {"op": "getAll", "sql": f"PRAGMA table_info({name})"})
        if st != 200:
            return f"pragma {name} HTTP {st} {info!r}", {}
        tables[name] = info.get("rows") or []
    return None, tables


def columns_of(tables: dict[str, list[dict]]) -> set[str]:
    names: set[str] = set()
    for cols in tables.values():
        for col in cols:
            names.add(str(col["name"]).lower())
    return names


def rows_mention(instance_id: str, needle: str) -> bool:
    err, tables = user_tables(instance_id)
    if err or not tables:
        return False
    needle_l = needle.lower()
    for table in tables:
        status, body = rpc(instance_id, {"op": "getAll", "sql": f"SELECT * FROM {table} LIMIT 50"})
        if status != 200:
            continue
        blob = json.dumps(body.get("rows") or []).lower()
        if needle_l in blob:
            return True
    return False


def pick_app(seen: set[str]) -> dict | None:
    created = [a for a in apps_in_personal() if a["id"] not in seen]
    if not created:
        return None
    by_slug = next((a for a in created if a.get("slug") == SLUG_WANT), None)
    if by_slug:
        return by_slug
    built = [a for a in created if (a.get("working_version") or {}).get("bundle_path")]
    return (built or created)[-1]


def history(app_id: str) -> list[dict]:
    status, body = api("GET", f"/api/platform/apps/{app_id}/history")
    if status != 200:
        fail(f"GET history: HTTP {status} {body!r}", "history_api")
    return body.get("commits") or []


async def run_turn(thread_id: str, label: str, prompt: str) -> list[dict]:
    try:
        frames = await turn(thread_id, prompt)
    except TimeoutError:
        fail(f"{label}: timeout after {TURN_TIMEOUT_S}s", "timeout")
    except Exception as exc:
        fail(f"{label}: turn_exception {exc!r}", "turn_exception")
    record_tools(frames)
    preview_frames(label, prompt, frames)
    return frames


def new_thread() -> str:
    status, thread = api("POST", "/api/threads", {})
    if status != 201:
        fail(f"create_thread HTTP {status}", "create_thread")
    return thread["id"]


async def create_and_iterate() -> tuple[dict, dict, dict]:
    seen = {a["id"] for a in apps_in_personal()}
    thread = new_thread()
    create_prompt = (
        f"Make me a grocery list app in my personal space. Use create_app with slug "
        f'"{SLUG_WANT}" and template "grocery-list", then call build_app until it '
        "succeeds. Don't ask questions — just build it."
    )
    app = None
    frames: list[dict] = []
    for attempt in (1, 2):
        frames = await run_turn(thread, f"create attempt {attempt}/2", create_prompt)
        turn_fail = classify_turn(frames)
        app = pick_app(seen)
        if app and build_succeeded(frames, app):
            break
        log(f"  WARN: create attempt {attempt}: turn={turn_fail} app={None if app is None else app.get('id')}")
        if attempt == 2:
            if app is None:
                fail("agent never created an app in /personal/Apps", "no_app")
            if not build_succeeded(frames, app):
                fail("create turn did not produce a successful build_app", "build_failed")
    assert app is not None
    for f in frames:
        if f.get("type") == "tool_start" and f.get("name") == "create_app":
            REPORT["template"] = (f.get("args") or {}).get("template") or "grocery-list"

    inst, personal = instance_for(app)
    if inst is None:
        fail("created app has no instance in the personal space", "no_instance")
    assert inst is not None

    iterate_prompt = (
        f"The grocery list app {app.get('slug') or SLUG_WANT} (instance {inst['id']}) should "
        "have a quantity on each item. If schema.sql already has a quantity (or qty) column, "
        "just call build_app once more so we have a fresh build. If it doesn't, add a "
        "quantity column (TEXT NOT NULL DEFAULT ''), show it in the UI, then build_app until "
        "it succeeds. Don't ask questions — just do it."
    )
    first_commits = history(app["id"])
    first_commit = next((c["id"] for c in reversed(first_commits) if c.get("id")), None)

    frames = []
    for attempt in (1, 2):
        frames = await run_turn(thread, f"iterate attempt {attempt}/2", iterate_prompt)
        turn_fail = classify_turn(frames)
        status, fresh = api("GET", f"/api/platform/apps/{app['id']}")
        if status == 200:
            app = fresh
        if build_succeeded(frames, app):
            break
        log(f"  WARN: iterate attempt {attempt}: turn={turn_fail}")
        if attempt == 2:
            fail("iterate turn did not produce a successful build_app", "build_failed")

    err, tables = user_tables(inst["id"])
    if err:
        fail(f"schema_error: {err}", "schema_error")
    have = columns_of(tables)
    if not any(col in have for col in QTY_COLS):
        fail(f"after iterate, no quantity column (have {sorted(have)})", "schema_mismatch")
    log(f"ok   app {app['id']} slug={app.get('slug')} instance={inst['id']} columns={sorted(have)}")
    REPORT["app_id"] = app["id"]
    REPORT["instance_id"] = inst["id"]
    REPORT["slug"] = app.get("slug")
    REPORT["first_commit"] = first_commit
    REPORT["tables"] = sorted(tables)
    REPORT["columns"] = sorted(have)
    return app, inst, personal


def wait_state(key: str, timeout_s: int, what: str) -> dict[str, Any]:
    deadline = time.time() + timeout_s
    last: dict[str, Any] = {}
    while time.time() < deadline:
        last = read_state()
        if last.get(key):
            return last
        if last.get("ui_error"):
            fail(f"UI: {last['ui_error']}", "ui_error")
        time.sleep(0.4)
    fail(f"timed out waiting for {what}", "ui_timeout")
    raise AssertionError


async def roundtrip(app: dict, inst: dict) -> None:
    thread = new_thread()
    read_prompt = (
        f"In the grocery list app {app.get('slug')} (instance {inst['id']}), use app_sql to "
        f"SELECT the items from the database. Tell me the names you see, including whether "
        f"{UI_ITEM} is there. Don't change anything."
    )
    for attempt in (1, 2):
        frames = await run_turn(thread, f"read attempt {attempt}/2", read_prompt)
        blob = json.dumps(frames).lower()
        previews = " ".join(tool_previews(frames, "app_sql")).lower()
        if UI_ITEM.lower() in previews or UI_ITEM.lower() in blob:
            log(f"ok   agent app_sql saw {UI_ITEM}")
            break
        log(f"  WARN: read attempt {attempt}: app_sql did not mention {UI_ITEM}")
        if attempt == 2:
            fail(f"agent never read {UI_ITEM} via app_sql", "agent_did_not_read")

    write_prompt = (
        f"Add an item named exactly {AGENT_ITEM} to the grocery list app {app.get('slug')} "
        f"(instance {inst['id']}) using app_action addItem with that name, or app_sql INSERT "
        "if there is no addItem action. Don't ask questions — just add it."
    )
    for attempt in (1, 2):
        frames = await run_turn(thread, f"write attempt {attempt}/2", write_prompt)
        tools_ok = tool_starts(frames, "app_action") or tool_starts(frames, "app_sql")
        if tools_ok and rows_mention(inst["id"], AGENT_ITEM):
            log(f"ok   agent wrote {AGENT_ITEM} (in the instance database)")
            break
        log(
            f"  WARN: write attempt {attempt}: actions={tool_starts(frames, 'app_action')} "
            f"sql={tool_starts(frames, 'app_sql')} in_db={rows_mention(inst['id'], AGENT_ITEM)}"
        )
        if attempt == 2:
            fail(f"agent never wrote {AGENT_ITEM} via app_action/app_sql", "agent_did_not_write")


def revert_change(app: dict, first_commit: str | None) -> None:
    commits = history(app["id"])
    if not commits:
        fail("no history commits to revert", "revert_failed")
    target = first_commit or commits[-1]["id"]
    status, body = api(
        "POST",
        f"/api/platform/apps/{app['id']}/revert",
        {"commit": target},
        timeout=180,
    )
    if status != 200:
        fail(f"POST revert: HTTP {status} {body!r}", "revert_failed")
    after = history(app["id"])
    if not after:
        fail("history empty after revert", "revert_failed")
    head = after[0]
    if not (head.get("kind") == "revert" or head.get("id") == target or head.get("current")):
        fail(f"after revert, unexpected head {head!r}", "revert_failed")
    if not (body.get("ok") or (body.get("build") or {}).get("bundle_path") or body.get("commit")):
        # AppRevertOut is AppBuildOut plus commit; ok True means rebuild succeeded.
        if body.get("ok") is False:
            fail(f"revert rebuild failed: {body.get('diagnostics')!r}", "revert_failed")
    log(f"ok   reverted to {target[:12]}…; head {head.get('kind')} {head.get('id', '')[:12]} ok={body.get('ok')}")
    REPORT["revert"] = {"target": target, "head": head.get("id"), "kind": head.get("kind"), "ok": body.get("ok")}


def start_ui() -> subprocess.Popen:
    env = os.environ.copy()
    return subprocess.Popen(
        ["node", str(HERE / "g13_grocery_smoke.mjs")],
        cwd=str(HERE),
        env=env,
    )


async def main() -> None:
    if not COOKIE:
        fail("E2E_AUTH_COOKIE is not set", "auth")
    if not os.environ.get("G13_USER") or not os.environ.get("G13_PASSWORD"):
        fail("G13_USER / G13_PASSWORD are not set", "auth")

    app, inst, personal = await create_and_iterate()
    write_state(
        {
            "app_id": app["id"],
            "instance_id": inst["id"],
            "slug": app.get("slug") or SLUG_WANT,
            "space_slug": personal["slug"],
            "ui_item": UI_ITEM,
            "agent_item": AGENT_ITEM,
            "ui_written": False,
            "agent_written": False,
        }
    )

    log("opening the runner in Chromium…")
    proc = start_ui()
    try:
        wait_state("ui_written", 120, "the UI to add the first item")
        if not rows_mention(inst["id"], UI_ITEM):
            fail(f"UI add of {UI_ITEM} is not in the instance database", "ui_write_missing")
        log(f"ok   UI wrote {UI_ITEM}; instance database has it")

        await roundtrip(app, inst)
        write_state({"agent_written": True})

        rc = proc.wait(timeout=90)
        if rc != 0:
            fail(f"runner UI exited {rc} (agent row not shown?)", "ui_did_not_show_agent_row")
        log(f"ok   runner showed {AGENT_ITEM} after the agent's write")
    except subprocess.TimeoutExpired:
        proc.kill()
        fail("runner UI did not finish after the agent wrote a row", "ui_did_not_show_agent_row")
    finally:
        if proc.poll() is None:
            proc.kill()

    revert_change(app, REPORT.get("first_commit"))
    REPORT["pass"] = True
    REPORT["taxonomy"] = "pass"
    write_report()
    log(f"wrote {OUT_DIR / 'g13-last-report.json'}")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        raise SystemExit(130)

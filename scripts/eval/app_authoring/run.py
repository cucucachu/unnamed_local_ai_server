#!/usr/bin/env python3
"""M13-03: drive the real local model through ≥10 app-authoring prompts.

Pass for a case = the agent created an app that **builds** (the platform
build includes the smoke render) **and** a scripted check of the data model
succeeds (expected columns exist; insert a row and read it back).

M22-19 adds `"kind": "customize"` cases: install a template, then ask for a
change the way the runner's "Ask the agent" panel does (a thread titled
"Ask: <app>" whose first message is the app context, then the prompt).
Pass = the turn's last `build_app` succeeded **and** the change is there:
`field` (the column exists and the source uses it), `screen` (a new route
file under `app/`) or `chart` (a source file uses a `@homeai/ui` chart).

Env: E2E_AUTH_COOKIE, E2E_BASE (default http://localhost). Optional:
EVAL_ONLY=<id>, EVAL_LIMIT=<n>, EVAL_TURN_TIMEOUT_S (default 1500),
EVAL_OUT (default this directory), EVAL_AGENT=candidate (M16: send every
request to the agent-candidate service, i.e. the candidate model).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from websockets.asyncio.client import connect

HERE = Path(__file__).resolve().parent
CASES_PATH = HERE / "cases.json"
BASE = os.environ.get("E2E_BASE", "http://localhost").rstrip("/")
COOKIE = os.environ.get("E2E_AUTH_COOKIE", "")
TURN_TIMEOUT_S = int(os.environ.get("EVAL_TURN_TIMEOUT_S", "1500"))
AGENT_HEADERS = {"X-HomeAI-Agent": "candidate"} if os.environ.get("EVAL_AGENT") == "candidate" else {}


def fail(msg: str) -> None:
    print(f"FAIL: {msg}", file=sys.stderr)
    sys.exit(1)


def api(method: str, path: str, body: Any = None, timeout: int = 60) -> tuple[int, Any]:
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(
        f"{BASE}{path}",
        data=data,
        method=method,
        headers={"Cookie": COOKIE, "Content-Type": "application/json", **AGENT_HEADERS},
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


def download(path: str) -> tuple[int, bytes]:
    q = urllib.parse.quote(path)
    req = urllib.request.Request(
        f"{BASE}/api/platform/files/download?path={q}",
        headers={"Cookie": COOKIE},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def rpc(instance_id: str, payload: dict, timeout: int = 30) -> tuple[int, Any]:
    return api(
        "POST",
        f"/api/platform/apps/instances/{instance_id}/rpc",
        payload,
        timeout=timeout,
    )


async def turn(thread_id: str, prompt: str) -> list[dict]:
    ws_base = BASE.replace("http", "ws", 1)
    frames: list[dict] = []
    async with connect(
        f"{ws_base}/ws/chat/{thread_id}",
        additional_headers={"Cookie": COOKIE, **AGENT_HEADERS},
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


def preview_frames(case_id: str, prompt: str, frames: list[dict]) -> None:
    print(f"\n=== {case_id} ===")
    print(f"prompt: {prompt[:180]}{'…' if len(prompt) > 180 else ''}")
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


def apps_in_personal() -> list[dict]:
    status, body = api("GET", "/api/platform/apps")
    if status != 200:
        fail(f"GET /apps: HTTP {status} {body!r}")
    return [a for a in body["apps"] if (a.get("source_path") or "").startswith("/personal/Apps/")]


def instance_for(app: dict) -> dict | None:
    status, spaces = api("GET", "/api/platform/spaces")
    if status != 200:
        fail(f"GET /spaces: HTTP {status} {spaces!r}")
    personal = next(s for s in spaces["spaces"] if s["kind"] == "personal")
    status, body = api("GET", f"/api/platform/spaces/{personal['id']}/instances")
    if status != 200:
        fail(f"GET instances: HTTP {status} {body!r}")
    return next((i for i in body["instances"] if i["app_id"] == app["id"]), None)


def user_tables(instance_id: str) -> tuple[str | None, dict[str, list[dict]]]:
    """(error, {table: pragma_table_info rows})."""
    status, body = rpc(
        instance_id,
        {"op": "getAll", "sql": "SELECT name, sql FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"},
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


def schema_ok(tables: dict[str, list[dict]], groups: list[list[str]]) -> str | None:
    if not tables:
        return "no user tables"
    have = columns_of(tables)
    missing = []
    for group in groups:
        if not any(name.lower() in have for name in group):
            missing.append("/".join(group))
    if missing:
        return "missing columns: " + ", ".join(missing) + f" (have {sorted(have)})"
    return None


def probe_value(col: dict) -> Any:
    name = str(col["name"]).lower()
    typ = str(col.get("type") or "").upper()
    if "day" in name or name.endswith("_at") or "date" in name:
        return "2026-09-28"
    if any(k in name for k in ("qty", "quantity", "amount", "count")):
        return "2" if "INT" not in typ else 2
    if any(k in typ for k in ("INT", "REAL", "NUM")):
        return 1
    return "eval-probe"


def write_roundtrip(instance_id: str, tables: dict[str, list[dict]]) -> str | None:
    table, cols = next(iter(tables.items()))
    assign: list[tuple[str, Any]] = []
    for col in cols:
        if col.get("pk") and str(col.get("type") or "").upper().startswith("INT"):
            continue
        if col.get("dflt_value") is not None:
            continue
        if not col.get("notnull"):
            continue
        assign.append((col["name"], probe_value(col)))
    if not assign:
        for col in cols:
            if col.get("pk"):
                continue
            assign.append((col["name"], probe_value(col)))
            break
    if assign:
        names = ", ".join(a[0] for a in assign)
        placeholders = ", ".join("?" for _ in assign)
        sql = f"INSERT INTO {table} ({names}) VALUES ({placeholders})"
        params = [a[1] for a in assign]
        status, body = rpc(instance_id, {"op": "run", "sql": sql, "params": params})
    else:
        status, body = rpc(instance_id, {"op": "run", "sql": f"INSERT INTO {table} DEFAULT VALUES"})
    if status != 200:
        return f"insert HTTP {status} {body!r}"
    status, body = rpc(instance_id, {"op": "getAll", "sql": f"SELECT * FROM {table} LIMIT 5"})
    if status != 200:
        return f"select HTTP {status} {body!r}"
    if not body.get("rows"):
        return "select returned 0 rows after insert"
    return None


def walk_files(root: str) -> list[str]:
    status, body = api("GET", f"/api/platform/files?path={urllib.parse.quote(root)}")
    if status != 200:
        return []
    out: list[str] = []
    for entry in body.get("entries") or []:
        path = entry["path"]
        if entry["type"] == "dir":
            out.extend(walk_files(path))
        else:
            out.append(path)
    return out


def scan_hooks(source_path: str) -> dict[str, bool]:
    found = {
        "useDatabase": False,
        "useSQLiteContext": False,
        "from_homeai_sdk": False,
        "from_expo_sqlite": False,
    }
    for path in walk_files(source_path):
        if not path.endswith((".ts", ".tsx", ".js", ".jsx")):
            continue
        status, data = download(path)
        if status != 200:
            continue
        text = data.decode("utf-8", "replace")
        if "useDatabase" in text:
            found["useDatabase"] = True
        if "useSQLiteContext" in text:
            found["useSQLiteContext"] = True
        if re.search(r"""from\s+['"]@homeai/sdk['"]""", text):
            found["from_homeai_sdk"] = True
        if re.search(r"""from\s+['"]expo-sqlite['"]""", text):
            found["from_expo_sqlite"] = True
    return found


CHART = re.compile(r"\b(BarChart|LineChart|PieChart)\b")
CONTEXT_FILES = ("app.json", "AGENT.md", "schema.sql")


def personal_space() -> dict:
    status, spaces = api("GET", "/api/platform/spaces")
    if status != 200:
        fail(f"GET /spaces: HTTP {status} {spaces!r}")
    return next(s for s in spaces["spaces"] if s["kind"] == "personal")


def read_text(path: str) -> str:
    status, data = download(path)
    return data.decode("utf-8", "replace") if status == 200 else ""


def source_texts(source_path: str) -> dict[str, str]:
    return {p: read_text(p) for p in walk_files(source_path) if p.endswith((".ts", ".tsx", ".js", ".jsx"))}


def context_message(space: dict, app: dict, instance_id: str, prompt: str) -> str:
    """The panel's first message: services/frontend/lib/appAgent.ts `seedUserMessage`."""
    source = app.get("source_path")
    lines = [
        "App context for this thread",
        f"Instance: {instance_id}",
        f"Space: {space['slug']} ({space['name']}, role {space.get('role') or 'none'})",
        f"App: {app['name']} ({app['id']})",
    ]
    if source:
        lines.append(f"Source: {source}")
    for name in CONTEXT_FILES:
        lines += [f"--- {name} ---", (read_text(f"{source}/{name}") if source else "") or "(empty or unavailable)"]
    return "\n".join(lines) + "\n---\n" + prompt


def last_build(frames: list[dict]) -> str | None:
    """The result of the turn's last `build_app` call, or None if it never built."""
    builds = [f for f in frames if f.get("type") == "tool_end" and f.get("name") == "build_app"]
    return (builds[-1].get("result_preview") or "") if builds else None


def tool_log(frames: list[dict]) -> list[dict]:
    return [
        {
            "name": f.get("name"),
            "status": f.get("status"),
            "args": next(
                (s.get("args") for s in frames if s.get("type") == "tool_start" and s.get("tool_call_id") == f.get("tool_call_id")),
                None,
            ),
            "preview": (f.get("result_preview") or "")[:500],
        }
        for f in frames
        if f.get("type") == "tool_end"
    ]


def change_check(case: dict, inst_id: str, source: str, before: set[str]) -> tuple[str | None, dict]:
    """(failure, details) for a customize case's requested change."""
    texts = source_texts(source)
    if case["change"] == "field":
        err, tables = user_tables(inst_id)
        if err:
            return f"schema_error: {err}", {}
        cols = [str(c["name"]) for c in tables.get(case["table"], [])]
        if case["column"] not in cols:
            return f"schema_mismatch: no {case['table']}.{case['column']} (have {cols})", {"columns": cols}
        used = sorted(p for p, t in texts.items() if case["column"] in t)
        if not used:
            return f"not_in_source: nothing under {source} uses {case['column']}", {"columns": cols}
        return None, {"columns": cols, "used_in": used}
    if case["change"] == "screen":
        added = sorted(
            p
            for p in set(walk_files(source)) - before
            if p.startswith(f"{source}/app/") and p.endswith((".tsx", ".ts", ".jsx", ".js")) and not p.rsplit("/", 1)[-1].startswith("_")
        )
        return (None if added else "no_new_screen"), {"added": added}
    charts = sorted(p for p, t in texts.items() if CHART.search(t) and "@homeai/ui" in t)
    return (None if charts else "no_chart"), {"charts_in": charts}


async def run_customize(case: dict, seen_ids: set[str]) -> dict:
    result: dict[str, Any] = {
        "id": case["id"],
        "kind": "customize",
        "change": case["change"],
        "template": case["template"],
        "prompt": case["prompt"],
        "pass": False,
        "failure": None,
        "app_id": None,
        "instance_id": None,
        "source_path": None,
        "details": {},
        "duration_ms": None,
        "tools": [],
    }
    space = personal_space()
    status, out = api("POST", f"/api/platform/spaces/{space['id']}/app-templates/{case['template']}/install", {}, timeout=600)
    if status not in (200, 201) or not (out or {}).get("build", {}).get("ok"):
        result["failure"] = f"install_failed: HTTP {status} {str(out)[:300]}"
        return result
    app, inst = out["app"], out["instance"]
    seen_ids.add(app["id"])
    result.update(app_id=app["id"], instance_id=inst["id"], source_path=app.get("source_path"))
    source = app["source_path"]
    before = set(walk_files(source))

    status, thread = api("POST", "/api/threads", {"title": f"Ask: {app['name']}"})
    if status != 201:
        result["failure"] = f"create_thread HTTP {status}"
        return result
    try:
        frames = await turn(thread["id"], context_message(space, app, inst["id"], case["prompt"]))
    except TimeoutError:
        result["failure"] = "timeout"
        preview_frames(case["id"], case["prompt"], [])
        return result
    except Exception as exc:
        result["failure"] = f"turn_exception: {exc!r}"
        return result
    result["duration_ms"] = (frames[-1] if frames else {}).get("duration_ms")
    result["tools"] = tool_log(frames)
    preview_frames(case["id"], case["prompt"], frames)

    turn_fail = classify_turn(frames)
    if turn_fail:
        result["failure"] = turn_fail
        return result
    build = last_build(frames)
    if build is None:
        result["failure"] = "no_build"
        return result
    if not build.startswith("Build succeeded"):
        result["failure"] = f"build_failed: {build[:200]}"
        return result
    failure, details = change_check(case, inst["id"], source, before)
    result["details"] = details
    result["failure"] = failure
    result["pass"] = failure is None
    return result


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


def build_succeeded(frames: list[dict], app: dict | None) -> bool:
    for frame in frames:
        if frame.get("type") == "tool_end" and frame.get("name") == "build_app":
            preview = frame.get("result_preview") or ""
            if preview.startswith("Build succeeded"):
                return True
    version = (app or {}).get("working_version") or {}
    return bool(version.get("bundle_path") or version.get("commit"))


async def run_case(case: dict, seen_ids: set[str]) -> dict:
    if case.get("kind") == "customize":
        return await run_customize(case, seen_ids)
    result: dict[str, Any] = {
        "id": case["id"],
        "kind": "create",
        "prompt": case["prompt"],
        "pass": False,
        "failure": None,
        "template": None,
        "app_id": None,
        "instance_id": None,
        "source_path": None,
        "hooks": {},
        "tables": [],
        "columns": [],
        "duration_ms": None,
        "tools": [],
    }
    status, thread = api("POST", "/api/threads", {})
    if status != 201:
        result["failure"] = f"create_thread HTTP {status}"
        return result
    try:
        frames = await turn(thread["id"], case["prompt"])
    except TimeoutError:
        result["failure"] = "timeout"
        preview_frames(case["id"], case["prompt"], [])
        return result
    except Exception as exc:
        result["failure"] = f"turn_exception: {exc!r}"
        return result

    end = frames[-1] if frames else {}
    result["duration_ms"] = end.get("duration_ms")
    result["tools"] = tool_log(frames)
    for frame in frames:
        if frame.get("type") == "tool_start" and frame.get("name") == "create_app":
            result["template"] = (frame.get("args") or {}).get("template") or "grocery-list"
    preview_frames(case["id"], case["prompt"], frames)

    turn_fail = classify_turn(frames)
    if turn_fail:
        result["failure"] = turn_fail
        return result

    created = [a for a in apps_in_personal() if a["id"] not in seen_ids]
    if not created:
        result["failure"] = "no_app"
        return result
    # Prefer a built app; otherwise the last one created.
    app = next((a for a in reversed(created) if (a.get("working_version") or {}).get("bundle_path")), created[-1])
    result["app_id"] = app["id"]
    result["source_path"] = app.get("source_path")
    inst = instance_for(app)
    if inst is None:
        result["failure"] = "no_instance"
        return result
    result["instance_id"] = inst["id"]
    result["hooks"] = scan_hooks(app["source_path"]) if app.get("source_path") else {}

    if not build_succeeded(frames, app):
        result["failure"] = "build_failed"
        return result

    err, tables = user_tables(inst["id"])
    if err:
        result["failure"] = f"schema_error: {err}"
        return result
    result["tables"] = sorted(tables)
    result["columns"] = sorted(columns_of(tables))
    schema_fail = schema_ok(tables, case.get("need_column_groups") or [])
    if schema_fail:
        result["failure"] = f"schema_mismatch: {schema_fail}"
        return result
    write_fail = write_roundtrip(inst["id"], tables)
    if write_fail:
        result["failure"] = f"write_failed: {write_fail}"
        return result
    result["pass"] = True
    return result


def summarize(all_results: list[dict]) -> dict[str, Any]:
    results = [r for r in all_results if r.get("kind") != "customize"]
    customize = [r for r in all_results if r.get("kind") == "customize"]
    passed = sum(1 for r in results if r["pass"])
    taxonomy: dict[str, list[str]] = {}
    hooks = {"useDatabase": 0, "useSQLiteContext": 0, "both": 0, "neither": 0}
    templates: dict[str, int] = {}
    for r in results:
        key = "pass" if r["pass"] else (r["failure"] or "unknown").split(":")[0]
        taxonomy.setdefault(key, []).append(r["id"])
        h = r.get("hooks") or {}
        db, sql = bool(h.get("useDatabase")), bool(h.get("useSQLiteContext"))
        if db and sql:
            hooks["both"] += 1
        elif db:
            hooks["useDatabase"] += 1
        elif sql:
            hooks["useSQLiteContext"] += 1
        else:
            hooks["neither"] += 1
        t = r.get("template") or "(none)"
        templates[t] = templates.get(t, 0) + 1
    n = len(results)
    return {
        "passed": passed,
        "total": n,
        "rate": f"{passed}/{n}",
        "bar_7_of_10": passed >= 7,
        "escalate_below_5": passed < 5,
        "taxonomy": {k: v for k, v in taxonomy.items()},
        "hooks": hooks,
        "templates": templates,
        "customize": {
            "passed": sum(1 for r in customize if r["pass"]),
            "total": len(customize),
            "rate": f"{sum(1 for r in customize if r['pass'])}/{len(customize)}",
            "by_case": {r["id"]: "pass" if r["pass"] else (r["failure"] or "unknown") for r in customize},
        },
    }


async def main() -> int:
    if not COOKIE:
        fail("E2E_AUTH_COOKIE is not set")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--only", default=os.environ.get("EVAL_ONLY") or "")
    parser.add_argument("--limit", type=int, default=int(os.environ.get("EVAL_LIMIT") or "0"))
    args = parser.parse_args()
    cases = json.loads(CASES_PATH.read_text())
    if args.only:
        cases = [c for c in cases if c["id"] == args.only]
        if not cases:
            fail(f"no case id {args.only!r}")
    if args.limit:
        cases = cases[: args.limit]

    status, body = api("GET", "/api/platform/tools")
    if status != 200:
        fail(f"GET /api/platform/tools: HTTP {status}")
    changes = [
        {"name": t["name"], "permission": "allow"}
        for t in body["tools"]
        if t["enabled"] and not t["permission_locked"] and t["permission"] != "allow"
    ]
    if changes:
        status, _ = api("PATCH", "/api/platform/tools", {"changes": changes})
        if status != 200:
            fail(f"allowing every tool: HTTP {status}")

    seen = {a["id"] for a in apps_in_personal()}
    results: list[dict] = []
    app_ids: list[str] = []
    try:
        for case in cases:
            result = await run_case(case, seen)
            results.append(result)
            if result.get("app_id"):
                seen.add(result["app_id"])
                app_ids.append(result["app_id"])
            print(f"  => {'PASS' if result['pass'] else 'FAIL'} {case['id']}" + (f" ({result['failure']})" if result["failure"] else ""))
    finally:
        out_dir = Path(os.environ.get("EVAL_OUT", str(HERE)))
        out_dir.mkdir(parents=True, exist_ok=True)
        summary = summarize(results)
        report = {"summary": summary, "results": results, "app_ids": app_ids}
        (out_dir / "last-report.json").write_text(json.dumps(report, indent=2, default=str) + "\n")
        ids_path = out_dir / "app-ids.txt"
        ids_path.write_text("".join(i + "\n" for i in app_ids))
        print("\n=== summary ===")
        print(json.dumps(summary, indent=2))
        print(f"wrote {out_dir / 'last-report.json'}")
    return 0 if summary["passed"] >= 7 or not summary["total"] else 1


if __name__ == "__main__":
    try:
        raise SystemExit(asyncio.run(main()))
    except KeyboardInterrupt:
        raise SystemExit(130)

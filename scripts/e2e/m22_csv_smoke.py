#!/usr/bin/env python3
"""M22-08: CSV export and import on the live stack. See m22_csv_smoke.sh.

Env: E2E_AUTH_COOKIE, E2E_BASE (default http://localhost), E2E_CSV_STATE.
"""

from __future__ import annotations

import asyncio
import os

import m21_search_smoke as m21
from m21_search_smoke import api, fail, log, turn

m21.PREFIX = "m22-csv"
STATE = os.environ["E2E_CSV_STATE"]
TODO = "/personal/todo-import.csv"
QUESTION = (
    f"Import {TODO} into the notes table of my Notes app (instance personal/notes). "
    "Then tell me how many rows you imported."
)


def personal_space() -> dict:
    return next(s for s in api("GET", "/api/platform/spaces")["spaces"] if s["kind"] == "personal")


def notes_instance(space_id: str) -> dict:
    out = api(
        "POST",
        f"/api/platform/spaces/{space_id}/app-templates/notes/install",
        {"slug": "notes", "name": "Notes"},
        want=201,
    )
    with open(STATE, "a") as f:
        f.write(f"{out['app']['id']}\n")
    if not (out["build"] and out["build"]["ok"]):
        fail(f"notes didn't build: {out['build']!r}")
    return out["instance"]


def rpc(inst: dict, body: dict) -> dict:
    return api("POST", f"/api/platform/apps/instances/{inst['id']}/rpc", body)


def titles(inst: dict) -> list[str]:
    rows = rpc(inst, {"op": "getAll", "sql": "SELECT title FROM notes ORDER BY id"})["rows"]
    return [r["title"] for r in rows]


def write(path: str, content: str) -> None:
    api("POST", "/api/platform/files/write", {"path": path, "content": content})


def read(path: str) -> str:
    return api("POST", "/api/platform/files/read", {"path": path})["content"]


def main() -> None:
    inst = notes_instance(personal_space()["id"])
    url = f"/api/platform/apps/instances/{inst['id']}"
    for title, body in (("Milk", "semi-skimmed"), ("Call mum, Sunday", 'say "hi"')):
        rpc(inst, {"op": "run", "sql": "INSERT INTO notes (title, body) VALUES (?, ?)", "params": [title, body]})
    log("installed Notes and added two notes")

    out = api("POST", f"{url}/export", {}, want=201)
    folder = out["folder"]
    if not folder.startswith("/personal/Exports/notes-") or out["tables"] != [
        {"name": "notes", "file": "notes.csv", "rows": 2}
    ]:
        fail(f"unexpected export: {out!r}")
    csv_text, schema = read(f"{folder}/notes.csv"), read(f"{folder}/schema.sql")
    if '"Call mum, Sunday"' not in csv_text or "_created_by" not in csv_text:
        fail(f"notes.csv should quote the comma and keep the stamps: {csv_text[:400]!r}")
    if "CREATE TABLE notes" not in schema:
        fail(f"schema.sql: {schema[:200]!r}")
    again = api("POST", f"{url}/export", {"folder": folder}, want=409)
    if again["detail"] != "exists":
        fail(f"a second export into {folder} should be 409 exists: {again!r}")
    log(f"OK: exported to {folder} (notes.csv + schema.sql); no overwrite")

    # `id` would collide with the rows it came from; everything else round-trips.
    back = {"table": "notes", "path": f"{folder}/notes.csv", "mapping": {"title": "title", "body": "body", "pinned": "pinned"}}
    check = api("POST", f"{url}/import", {**back, "dry_run": True})
    if (check["rows"], check["dry_run"]) != (2, True) or len(titles(inst)) != 2:
        fail(f"the dry run should count 2 rows and change nothing: {check!r}")
    done = api("POST", f"{url}/import", back)
    if done["rows"] != 2 or titles(inst) != ["Milk", "Call mum, Sunday"] * 2:
        fail(f"the round trip should add the same two notes: {done!r}, {titles(inst)!r}")
    log("OK: the export imports back (dry run, then 2 rows)")

    write("/personal/semi.csv", "\ufeffHeading;Text\nBread;wholemeal\nEggs;a dozen\n")
    semi = {"table": "notes", "path": "/personal/semi.csv", "delimiter": ";"}
    plan = api("POST", f"{url}/import", {**semi, "dry_run": True})
    if plan["unmatched"] != ["Heading", "Text"] or "title" not in plan["columns"]:
        fail(f"a dry run should report the unmatched headers: {plan!r}")
    mapped = api("POST", f"{url}/import", {**semi, "mapping": {"Heading": "title", "Text": "body"}})
    if mapped["rows"] != 2 or titles(inst)[-2:] != ["Bread", "Eggs"]:
        fail(f"the mapped semicolon file should add Bread and Eggs: {mapped!r}")
    log("OK: a semicolon file with a BOM and other headers imports with a mapping")

    write("/personal/bad.csv", "title,pinned\nFine,0\nBroken,5\n")
    bad = api("POST", f"{url}/import", {"table": "notes", "path": "/personal/bad.csv"}, want=422)
    if (bad["detail"], bad["line"]) != ("import_failed", 3) or len(titles(inst)) != 6:
        fail(f"a bad row on line 3 should import nothing: {bad!r}, {titles(inst)!r}")
    log(f"OK: a bad row imports nothing ({bad['message']!r}, line {bad['line']})")

    write(TODO, "title,body\nBuy stamps,first class\nFix the gate,the latch sticks\n")
    api("PATCH", "/api/platform/tools", {"changes": [{"name": "app_import", "permission": "allow"}]})
    for attempt in (1, 2):
        thread = api("POST", "/api/threads", {}, want=201)["id"]
        try:
            result = asyncio.run(turn(thread, QUESTION))
        finally:
            api("DELETE", f"/api/threads/{thread}", want=204)
        names = [t["name"] for t in result["tools"]]
        log(f"attempt {attempt}: tools {names}, reply {result['reply'].strip()[:200]!r}")
        now = titles(inst)
        if "app_import" in names and now[-2:] == ["Buy stamps", "Fix the gate"] and len(now) == 8:
            log("OK: the agent imported the file with app_import")
            return
        if attempt == 2 or len(now) != 6:
            fail(f"the agent should import {TODO} once with app_import: tools {names}, notes {now!r}")
        log("retrying once in a new chat")


if __name__ == "__main__":
    main()

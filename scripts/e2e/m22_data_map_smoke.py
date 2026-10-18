#!/usr/bin/env python3
"""M22-06: the data map on the live stack. See m22_data_map_smoke.sh.

Env: E2E_AUTH_COOKIE, E2E_BASE (default http://localhost), E2E_DATA_MAP_STATE.
"""

from __future__ import annotations

import asyncio
import json
import os

import m21_search_smoke as m21
from m21_search_smoke import api, fail, log, turn

m21.PREFIX = "m22-data-map"
STATE = os.environ["E2E_DATA_MAP_STATE"]
BUDGET = "/personal/Apps/budget"
APP_TOOLS = {"list_apps", "app_query", "app_update", "app_action", "create_app", "build_app"}
QUESTION = "Which of my apps track money? Answer in one line."

BUDGET_FILES = {
    "app.json": json.dumps(
        {
            "name": "Budget",
            "slug": "budget",
            "version": "1.0.0",
            "homeai": {
                "sdk": "1",
                "icon": "wallet-outline",
                "description": "Monthly spending by category.",
                "permissions": {},
            },
        },
        indent=2,
    ),
    "schema.sql": (
        "-- One row per purchase.\n"
        "CREATE TABLE expenses (\n"
        "  id INTEGER PRIMARY KEY,\n"
        "  amount_cents INTEGER NOT NULL, -- what it cost, in cents\n"
        "  category TEXT NOT NULL, -- groceries, rent, fun, ...\n"
        "  day TEXT NOT NULL -- YYYY-MM-DD\n"
        ");\n"
    ),
    "AGENT.md": "# Budget\n\nOne `expenses` table; the home screen counts them.\n",
    "app/_layout.tsx": (
        "import { Stack } from 'expo-router';\n\n"
        "export default function Layout() {\n"
        "  return <Stack />;\n"
        "}\n"
    ),
    "app/index.tsx": (
        "import { Text } from 'react-native';\n"
        "import { useQuery } from '@homeai/sdk';\n\n"
        "export default function Home() {\n"
        "  const { data } = useQuery<{ n: number }>('SELECT count(*) AS n FROM expenses');\n"
        "  return <Text testID=\"count\">{data?.[0]?.n ?? 0} expenses</Text>;\n"
        "}\n"
    ),
}


def personal_space() -> dict:
    return next(s for s in api("GET", "/api/platform/spaces")["spaces"] if s["kind"] == "personal")


def remember(app_id: str) -> None:
    with open(STATE, "a") as f:
        f.write(f"{app_id}\n")


def from_template(space_id: str, template: str, slug: str, name: str) -> dict:
    out = api(
        "POST",
        f"/api/platform/spaces/{space_id}/app-templates/{template}/install",
        {"slug": slug, "name": name},
        want=201,
    )
    remember(out["app"]["id"])
    if not (out["build"] and out["build"]["ok"]):
        fail(f"{template} didn't build: {out['build']!r}")
    return out["instance"]


def budget_app(space_id: str) -> dict:
    for rel, text in BUDGET_FILES.items():
        api("POST", "/api/platform/files/write", {"path": f"{BUDGET}/{rel}", "content": text})
    app = api("POST", "/api/platform/apps", {"source_path": BUDGET}, want=201)["app"]
    remember(app["id"])
    inst = api("POST", f"/api/platform/spaces/{space_id}/instances", {"app_id": app["id"]}, want=201)
    built = api("POST", f"/api/platform/apps/{app['id']}/build", {})
    if not built["ok"]:
        fail(f"Budget didn't build: {built['diagnostics']!r}")
    for cents, category in ((5_250, "groceries"), (120_000, "rent"), (1_800, "fun")):
        api(
            "POST",
            f"/api/platform/apps/instances/{inst['id']}/rpc",
            {
                "op": "run",
                "sql": "INSERT INTO expenses (amount_cents, category, day) VALUES (?, ?, ?)",
                "params": [cents, category, "2026-10-18"],
            },
        )
    return inst


def check_map(budget: dict) -> None:
    instances = api("GET", "/api/platform/data-map")["instances"]
    names = sorted(i["app"]["name"] for i in instances)
    if names != ["Budget", "Chores", "Habits"]:
        fail(f"the map should list the three apps: {names}")
    entry = next(i for i in instances if i["id"] == budget["id"])
    (expenses,) = entry["tables"]
    if (entry["description"], expenses["rows"], expenses["description"]) != (
        "Monthly spending by category.",
        3,
        "One row per purchase.",
    ):
        fail(f"Budget's entry: {entry!r}")
    if expenses["columns"].get("amount_cents") != "what it cost, in cents":
        fail(f"Budget's column descriptions: {expenses['columns']!r}")
    habits = next(i for i in instances if i["app"]["name"] == "Habits")
    if [t["name"] for t in habits["tables"]] != ["habits", "checkins"]:
        fail(f"Habits' tables: {habits['tables']!r}")
    log("OK: the data map lists the apps, row counts and descriptions")


def ask() -> tuple[list[str], str]:
    thread = api("POST", "/api/threads", {}, want=201)["id"]
    try:
        result = asyncio.run(turn(thread, QUESTION))
    finally:
        api("DELETE", f"/api/threads/{thread}", want=204)
    return [t["name"] for t in result["tools"]], result["reply"]


def main() -> None:
    space = personal_space()
    from_template(space["id"], "tracker", "habits", "Habits")
    from_template(space["id"], "list", "chores", "Chores")
    budget = budget_app(space["id"])
    log("installed and built Habits, Chores and Budget")
    check_map(budget)

    for attempt in (1, 2):
        tools, reply = ask()
        log(f"attempt {attempt}: tools {tools}, reply {reply.strip()!r}")
        if "budget" not in reply.lower():
            fail(f"the reply should name Budget: {reply!r}")
        used = [t for t in tools if t in APP_TOOLS]
        if not used:
            log("OK: a new chat named Budget from the map, without calling an app tool")
            return
        if attempt == 2:
            fail(f"the model called {used} before answering, twice")
        log(f"the model called {used}; retrying once in a new chat")


if __name__ == "__main__":
    main()

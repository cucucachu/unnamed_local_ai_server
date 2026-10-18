#!/usr/bin/env python3
"""M22-07: app rows in search on the live stack. See m22_app_search_smoke.sh.

Env: E2E_AUTH_COOKIE, E2E_BASE (default http://localhost), E2E_APP_SEARCH_STATE.
"""

from __future__ import annotations

import asyncio
import json
import os
import time

import m21_search_smoke as m21
from m21_search_smoke import api, fail, log, turn

m21.PREFIX = "m22-app-search"
STATE = os.environ["E2E_APP_SEARCH_STATE"]
RECIPES = "/personal/Apps/recipes"
INDEX_BUDGET_S = 180
# Shares no word with the dal row: only the vector half can find it.
PARAPHRASE = "spiced legume stew from south asia"
QUESTION = (
    "Search my files and apps: which of my saved recipes is a spiced legume stew from "
    "South Asia? Answer with just its name."
)

RECIPES_FILES = {
    "app.json": json.dumps(
        {
            "name": "Recipes",
            "slug": "recipes",
            "version": "1.0.0",
            "homeai": {
                "sdk": "1",
                "icon": "restaurant-outline",
                "description": "Saved recipes: ingredients and steps.",
                "permissions": {},
                "index": [
                    {
                        "table": "recipes",
                        "title": "name",
                        "text": ["ingredients", "steps"],
                        "fields": ["cuisine"],
                    }
                ],
            },
        },
        indent=2,
    ),
    "schema.sql": (
        "-- One row per recipe.\n"
        "CREATE TABLE recipes (\n"
        "  id INTEGER PRIMARY KEY,\n"
        "  name TEXT NOT NULL, -- what it's called\n"
        "  cuisine TEXT NOT NULL DEFAULT '', -- where it's from\n"
        "  ingredients TEXT NOT NULL DEFAULT '', -- one per line\n"
        "  steps TEXT NOT NULL DEFAULT '' -- how to make it\n"
        ");\n"
    ),
    "AGENT.md": "# Recipes\n\nOne `recipes` table, indexed for search; the home screen counts them.\n",
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
        "  const { data } = useQuery<{ n: number }>('SELECT count(*) AS n FROM recipes');\n"
        '  return <Text testID="count">{data?.[0]?.n ?? 0} recipes</Text>;\n'
        "}\n"
    ),
}

ROWS = [
    (
        "Dal tadka",
        "Indian",
        "red lentils\nturmeric\ncumin seeds\ngarlic\nghee",
        "Simmer the lentils with turmeric until soft, then pour over cumin and garlic fried in ghee.",
    ),
    (
        "Margherita pizza",
        "Italian",
        "dough\ntomato sauce\nmozzarella\nbasil",
        "Stretch the dough, top it, and bake in the hottest oven you have.",
    ),
    (
        "Fudge brownies",
        "American",
        "dark chocolate\nbutter\nsugar\neggs\nflour",
        "Melt the chocolate with the butter, whisk in the rest, bake 25 minutes.",
    ),
]


def personal_space() -> dict:
    return next(s for s in api("GET", "/api/platform/spaces")["spaces"] if s["kind"] == "personal")


def recipes_app(space_id: str) -> dict:
    for rel, text in RECIPES_FILES.items():
        api("POST", "/api/platform/files/write", {"path": f"{RECIPES}/{rel}", "content": text})
    app = api("POST", "/api/platform/apps", {"source_path": RECIPES}, want=201)["app"]
    with open(STATE, "a") as f:
        f.write(f"{app['id']}\n")
    inst = api("POST", f"/api/platform/spaces/{space_id}/instances", {"app_id": app["id"]}, want=201)
    built = api("POST", f"/api/platform/apps/{app['id']}/build", {})
    if not built["ok"]:
        fail(f"Recipes didn't build: {built['diagnostics']!r}")
    for name, cuisine, ingredients, steps in ROWS:
        api(
            "POST",
            f"/api/platform/apps/instances/{inst['id']}/rpc",
            {
                "op": "run",
                "sql": "INSERT INTO recipes (name, cuisine, ingredients, steps) VALUES (?, ?, ?, ?)",
                "params": [name, cuisine, ingredients, steps],
            },
        )
    return inst


def top_row(query: str) -> dict | None:
    found = api("POST", "/api/platform/files/search", {"query": query, "limit": 5})
    rows = [h for h in found["results"] if h["kind"] == "app_row"]
    return rows[0] if found["semantic"] and rows else None


def wait_for_dal() -> dict:
    deadline = time.monotonic() + INDEX_BUDGET_S
    while True:
        hit = top_row(PARAPHRASE)
        if hit and hit["app_row"]["title"] == "Dal tadka":
            return hit
        if time.monotonic() > deadline:
            fail(f"{PARAPHRASE!r} didn't find Dal tadka within {INDEX_BUDGET_S}s: {hit!r}")
        time.sleep(3)


def main() -> None:
    space = personal_space()
    inst = recipes_app(space["id"])
    log("built Recipes and added three recipes")

    started = time.monotonic()
    hit = wait_for_dal()
    ref = hit["app_row"]
    log(f"OK: a paraphrase finds the dal row by meaning ({time.monotonic() - started:.0f}s)")
    if (ref["instance_id"], ref["table"], ref["row_id"], ref["app"]["slug"]) != (
        inst["id"],
        "recipes",
        1,
        "recipes",
    ) or ref["space"]["kind"] != "personal":
        fail(f"unexpected reference: {ref!r}")
    exact = top_row("mozzarella")
    if not exact or exact["app_row"]["title"] != "Margherita pizza":
        fail(f"an exact ingredient should find the pizza first: {exact!r}")
    log("OK: an exact ingredient finds its row too")

    results = api(
        "POST",
        f"/api/platform/apps/instances/{inst['id']}/rpc",
        {"op": "search", "query": PARAPHRASE, "limit": 3},
    )["results"]
    if not results or (results[0]["table"], results[0]["id"], results[0]["title"]) != (
        "recipes",
        1,
        "Dal tadka",
    ):
        fail(f"the app's search() should find the dal first: {results!r}")
    log("OK: the app's own search (RPC op search) finds it by meaning")

    for attempt in (1, 2):
        thread = api("POST", "/api/threads", {}, want=201)["id"]
        try:
            result = asyncio.run(turn(thread, QUESTION))
        finally:
            api("DELETE", f"/api/threads/{thread}", want=204)
        names = [t["name"] for t in result["tools"]]
        reply = result["reply"].strip()
        log(f"attempt {attempt}: tools {names}, reply {reply!r}")
        if "search_files" in names and "dal" in reply.lower():
            log("OK: the agent found the recipe row with search_files")
            return
        if attempt == 2:
            fail(f"the agent should find Dal tadka with search_files: tools {names}, {reply!r}")
        log("retrying once in a new chat")


if __name__ == "__main__":
    main()

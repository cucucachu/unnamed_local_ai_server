#!/usr/bin/env python3
"""M21-05: reading past chats on the live stack. See m21_chats_smoke.sh.

Env: E2E_AUTH_COOKIE, E2E_BASE (default http://localhost).
"""

from __future__ import annotations

import asyncio

import m21_search_smoke
from m21_search_smoke import api, fail, log, turn

m21_search_smoke.PREFIX = "m21-chats"

FACT = "My favourite tree is the coast live oak, and I planted one in 2019."


def main() -> None:
    first = api("POST", "/api/threads", {"title": "Trees"}, want=201)["id"]
    asyncio.run(turn(first, f"{FACT} Just say OK."))
    log("told the agent a fact in one chat")

    second = api("POST", "/api/threads", {}, want=201)["id"]
    result = asyncio.run(
        turn(
            second,
            "Use list_chats and read_chat to look through my earlier chats: what's my favourite "
            "tree, and when did I plant one? Answer in one line.",
        )
    )
    names = [t["name"] for t in result["tools"]]
    if "read_chat" not in names:
        fail(f"the agent should read the earlier chat: tools {names}, reply {result['reply']!r}")
    read = next(t for t in result["tools"] if t["name"] == "read_chat")
    preview = read.get("result_preview") or ""
    if "coast live oak" not in preview or "User:" not in preview:
        fail(f"read_chat should show the earlier user message: {preview[:500]!r}")
    reply = result["reply"].lower()
    if "oak" not in reply or "2019" not in reply:
        fail(f"the reply should give the tree and the year: tools {names}, reply {reply!r}")
    log(f"OK: the agent read the earlier chat (tools {names}): {result['reply'].strip()!r}")
    for thread in (first, second):
        api("DELETE", f"/api/threads/{thread}", want=204)


if __name__ == "__main__":
    main()

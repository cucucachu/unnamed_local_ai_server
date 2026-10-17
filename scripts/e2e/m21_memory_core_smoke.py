#!/usr/bin/env python3
"""M21-06: the memory core on the live stack. See m21_memory_core_smoke.sh.

Env: E2E_AUTH_COOKIE, E2E_BASE (default http://localhost).
"""

from __future__ import annotations

import asyncio

import m21_search_smoke
from m21_search_smoke import api, fail, log, turn

m21_search_smoke.PREFIX = "m21-core"

CORE = """\
# About me
- Has a grey cat called Mochi.
- Allergic to hazelnuts.
- Lives in Tromsø; works as a ferry engineer.
"""


def main() -> None:
    api("POST", "/api/platform/files/write", {"path": "/personal/Memory/memory.md", "content": CORE})
    log("wrote /personal/Memory/memory.md")

    thread = api("POST", "/api/threads", {}, want=201)["id"]
    result = asyncio.run(turn(thread, "What do you know about me? Answer in one line."))
    names = [t["name"] for t in result["tools"]]
    reply = result["reply"].lower()
    if any(name in names for name in ("read_file", "search_files", "grep")):
        fail(f"the note should be in the prompt, not looked up: tools {names}, reply {reply!r}")
    if "mochi" not in reply or "hazelnut" not in reply:
        fail(f"the reply should come from the note: tools {names}, reply {reply!r}")
    log(f"OK: answered from the injected note (tools {names}): {result['reply'].strip()!r}")

    result = asyncio.run(turn(thread, "Please remember that my favourite colour is teal."))
    names = [t["name"] for t in result["tools"]]
    if any(name in names for name in ("write_file", "edit_file")):
        fail(f"remembering waits for the nightly routine: tools {names}")
    if "overnight" not in result["reply"].lower() and "tonight" not in result["reply"].lower():
        fail(f"the reply should say it's added overnight: {result['reply']!r}")
    log(f"OK: 'remember' waits for the night (tools {names}): {result['reply'].strip()!r}")
    api("DELETE", f"/api/threads/{thread}", want=204)


if __name__ == "__main__":
    main()

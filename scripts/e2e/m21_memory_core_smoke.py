#!/usr/bin/env python3
"""M21-06: the memory core on the live stack. See m21_memory_core_smoke.sh.

Env: E2E_AUTH_COOKIE, E2E_BASE (default http://localhost).
"""

from __future__ import annotations

import asyncio
import json
import urllib.parse
import urllib.request

import m21_search_smoke
from m21_search_smoke import BASE, COOKIE, TURN_TIMEOUT_S, api, connect, fail, log, turn

m21_search_smoke.PREFIX = "m21-core"

CORE = """\
# About me
- Has a grey cat called Mochi.
- Allergic to hazelnuts.
- Lives in Tromsø; works as a ferry engineer.
"""


def read_core() -> str:
    req = urllib.request.Request(
        f"{BASE}/api/platform/files/download?path={urllib.parse.quote('/personal/Memory/memory.md')}",
        headers={"Cookie": COOKIE},
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        return resp.read().decode()


async def turn_rejecting(thread_id: str, content: str) -> dict:
    """A turn whose approval cards are all rejected."""
    cards: list[str] = []
    tools: list[str] = []
    reply: list[str] = []
    ws_base = BASE.replace("http", "ws", 1)
    async with connect(f"{ws_base}/ws/chat/{thread_id}", additional_headers={"Cookie": COOKIE}) as ws:
        await ws.send(json.dumps({"type": "user_message", "content": content}))
        async with asyncio.timeout(TURN_TIMEOUT_S):
            async for raw in ws:
                frame = json.loads(raw)
                if frame["type"] == "tool_start":
                    tools.append(frame["name"])
                if frame["type"] == "token":
                    reply.append(frame.get("content") or "")
                if frame["type"] == "approval_request":
                    cards += [a["name"] for a in frame["actions"]]
                    decisions = [{"tool_call_id": a["tool_call_id"], "decision": "reject"} for a in frame["actions"]]
                    await ws.send(
                        json.dumps({"type": "approval_response", "interrupt_id": frame["interrupt_id"], "decisions": decisions})
                    )
                if frame["type"] == "error":
                    fail(f"turn error: {frame!r}")
                if frame["type"] == "turn_end":
                    break
    return {"tools": tools, "cards": cards, "reply": "".join(reply)}


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

    # Writes ask for this turn, whatever the user had (gate_full.sh allows every tool).
    writes = ("write_file", "edit_file")
    before = [{"name": t["name"], "permission": t["permission"]} for t in api("GET", "/api/platform/tools")["tools"] if t["name"] in writes]
    api("PATCH", "/api/platform/tools", {"changes": [{"name": n, "permission": "ask"} for n in writes]})
    try:
        result = asyncio.run(turn_rejecting(thread, "Please remember that my favourite colour is teal."))
    finally:
        api("PATCH", "/api/platform/tools", {"changes": before})
    if read_core() != CORE:
        fail(f"the note changed without asking: tools {result['tools']}, cards {result['cards']}")
    how = "asked to edit the notes (rejected)" if result["cards"] else "left it for the night"
    log(f"OK: 'remember' {how}, nothing written without asking: {result['reply'].strip()[:160]!r}")
    api("DELETE", f"/api/threads/{thread}", want=204)


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""M17-07: natural-language requests → routine schedules, on the real model.

A case passes when the agent's first `create_routine` approval card has the
expected schedule and the user's timezone. The card is then rejected, so
nothing is saved. "Tomorrow" is worked out in that timezone at run time.

Env: E2E_AUTH_COOKIE, E2E_BASE (default http://localhost). Optional:
EVAL_ONLY=<id>, EVAL_TURN_TIMEOUT_S (default 600).
Report: last-report.json next to this script.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from websockets.asyncio.client import connect

HERE = Path(__file__).resolve().parent
BASE = os.environ.get("E2E_BASE", "http://localhost").rstrip("/")
COOKIE = os.environ.get("E2E_AUTH_COOKIE", "")
TURN_TIMEOUT_S = int(os.environ.get("EVAL_TURN_TIMEOUT_S", "600"))
TIMEZONE = "America/Los_Angeles"


def cases() -> list[dict]:
    tomorrow = (datetime.now(ZoneInfo(TIMEZONE)) + timedelta(days=1)).date().isoformat()
    return [
        {
            "id": "weekdays",
            "prompt": "Every weekday at 7am, summarize the weather and my calendar.",
            "schedule": {"kind": "weekdays", "time": "07:00:00"},
        },
        {
            "id": "tomorrow",
            "prompt": "Remind me tomorrow at 9:30 in the morning to call the dentist.",
            "schedule": {"kind": "once", "at": f"{tomorrow}T09:30:00"},
        },
        {
            "id": "monthly",
            "prompt": "On the first of every month at 8pm, sum up what changed in my notes.",
            "schedule": {"kind": "monthly", "day": 1, "time": "20:00:00"},
        },
        {
            "id": "twice-weekly",
            "prompt": "Every Monday and Thursday at quarter past six in the evening, "
            "remind me to take out the bins.",
            "schedule": {"kind": "weekly", "days": ["mon", "thu"], "time": "18:15:00"},
        },
    ]


def api(method: str, path: str, body: Any = None) -> tuple[int, Any]:
    req = urllib.request.Request(
        f"{BASE}{path}",
        data=None if body is None else json.dumps(body).encode(),
        method=method,
        headers={"Cookie": COOKIE, "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            raw = resp.read()
            return resp.status, json.loads(raw) if raw else None
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")


async def run_case(case: dict) -> dict:
    status, thread = api("POST", "/api/threads", {})
    if status != 201:
        return {"id": case["id"], "ok": False, "why": f"POST /api/threads {status}"}
    ws_base = BASE.replace("http", "ws", 1)
    tools: list[str] = []
    card = None
    async with connect(
        f"{ws_base}/ws/chat/{thread['id']}", additional_headers={"Cookie": COOKIE}
    ) as ws:
        await ws.send(json.dumps({"type": "user_message", "content": case["prompt"]}))
        async with asyncio.timeout(TURN_TIMEOUT_S):
            async for raw in ws:
                frame = json.loads(raw)
                if frame["type"] == "tool_start":
                    tools.append(frame["name"])
                if frame["type"] == "approval_request":
                    card = card or next(
                        (a for a in frame["actions"] if a["name"] == "create_routine"), None
                    )
                    decisions = [
                        {"tool_call_id": a["tool_call_id"], "decision": "reject"}
                        for a in frame["actions"]
                    ]
                    await ws.send(
                        json.dumps(
                            {
                                "type": "approval_response",
                                "interrupt_id": frame["interrupt_id"],
                                "decisions": decisions,
                            }
                        )
                    )
                if frame["type"] in ("turn_end", "error"):
                    break
    result: dict[str, Any] = {"id": case["id"], "tools": tools, "expected": case["schedule"]}
    if card is None:
        return result | {"ok": False, "why": "no create_routine approval card"}
    got = card["args"].get("schedule")
    result |= {"got": got, "timezone": card["args"].get("timezone"), "card": card["description"]}
    if got != case["schedule"]:
        return result | {"ok": False, "why": "wrong schedule"}
    if result["timezone"] != TIMEZONE:
        return result | {"ok": False, "why": "wrong timezone"}
    return result | {"ok": True}


async def main() -> None:
    if not COOKIE:
        sys.exit("E2E_AUTH_COOKIE is required (run through run.sh)")
    status, _ = api("PUT", "/api/settings", {"timezone": TIMEZONE})
    if status != 200:
        sys.exit(f"PUT /api/settings {status}")
    only = os.environ.get("EVAL_ONLY")
    results = []
    for case in cases():
        if only and case["id"] != only:
            continue
        result = await run_case(case)
        results.append(result)
        mark = "PASS" if result["ok"] else f"FAIL ({result['why']})"
        print(f"{case['id']}: {mark}; tools {result.get('tools')}; got {result.get('got')}")
    passed = sum(r["ok"] for r in results)
    report = {"passed": passed, "total": len(results), "results": results}
    (HERE / "last-report.json").write_text(json.dumps(report, indent=2) + "\n")
    print(f"\n{passed}/{len(results)} passed")
    if passed != len(results):
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())

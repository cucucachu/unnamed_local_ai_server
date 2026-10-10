#!/usr/bin/env python3
"""#326: pre-approving a routine, on the live stack and the real model.

An ask-mode routine (made through the API) is run now and stops on its
write; answering with `allow_writes` finishes the run without another
stop, writes the file, and leaves the routine on "allow_writes".

Env: E2E_AUTH_COOKIE, E2E_BASE (default http://localhost).
Deletes its routine, threads and file on the way out.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from websockets.asyncio.client import connect

BASE = os.environ.get("E2E_BASE", "http://localhost").rstrip("/")
COOKIE = os.environ.get("E2E_AUTH_COOKIE", "")
TURN_TIMEOUT_S = 600
NOTE = "/personal/preapproval-smoke.md"


def log(msg: str) -> None:
    print(f"[preapproval] {time.strftime('%H:%M:%S')} {msg}", flush=True)


def fail(msg: str) -> None:
    log(f"FAIL: {msg}")
    raise SystemExit(1)


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
        raw = e.read()
        try:
            return e.code, json.loads(raw) if raw else None
        except json.JSONDecodeError:
            return e.code, raw.decode("utf-8", "replace")


async def frames_until_turn_end(ws: Any) -> list[dict]:
    frames: list[dict] = []
    async with asyncio.timeout(TURN_TIMEOUT_S):
        async for raw in ws:
            frame = json.loads(raw)
            frames.append(frame)
            if frame["type"] in ("turn_end", "error"):
                return frames
    return frames


def wait_run(routine_id: str, want: set[str], budget_s: int = TURN_TIMEOUT_S) -> dict:
    deadline = time.monotonic() + budget_s
    while time.monotonic() < deadline:
        status, runs = api("GET", f"/api/routines/{routine_id}/runs")
        if status == 200 and runs and runs[0]["status"] in want:
            return runs[0]
        time.sleep(3)
    fail(f"run never reached {sorted(want)}")
    return {}


async def approve_with_allow_writes(thread_id: str, pending: dict) -> list[dict]:
    ws_base = BASE.replace("http", "ws", 1)
    async with connect(
        f"{ws_base}/ws/chat/{thread_id}", additional_headers={"Cookie": COOKIE}
    ) as ws:
        await ws.send(
            json.dumps(
                {
                    "type": "approval_response",
                    "interrupt_id": pending["interrupt_id"],
                    "decisions": [
                        {"tool_call_id": a["tool_call_id"], "decision": "approve"}
                        for a in pending["actions"]
                    ],
                    "allow_writes": True,
                }
            )
        )
        return await frames_until_turn_end(ws)


def check_allow_writes_from_a_run(routines: list[str], threads: list[str]) -> None:
    status, routine = api(
        "POST",
        "/api/routines",
        {
            "name": "e2e preapproval",
            "prompt": f"Write the text 'one' to {NOTE}, then write the text 'two' to "
            f"{NOTE}.second. Use write_file for both; don't run code.",
            "schedule": {"kind": "daily", "time": "03:00"},
            "timezone": "UTC",
            "approval_mode": "ask",
        },
    )
    if status not in (200, 201):
        fail(f"create routine: HTTP {status} {routine!r}")
    routines.append(routine["id"])
    status, run = api("POST", f"/api/routines/{routine['id']}/run")
    if status != 202:
        fail(f"run now: HTTP {status} {run!r}")
    threads.append(run["thread_id"])
    run = wait_run(routine["id"], {"waiting_approval", "succeeded", "failed"})
    if run["status"] != "waiting_approval":
        fail(f"an ask-mode run didn't stop for its write: {run['status']}")
    _, state = api("GET", f"/api/threads/{run['thread_id']}/state")
    pending = state["pending_approval"]
    if not pending.get("can_allow_writes"):
        fail(f"no can_allow_writes on the pending approval: {pending!r}")

    frames = asyncio.run(approve_with_allow_writes(run["thread_id"], pending))
    if any(f.get("type") == "approval_request" for f in frames):
        fail("the run stopped again after allow_writes")
    run = wait_run(routine["id"], {"succeeded", "failed", "waiting_approval"})
    if run["status"] != "succeeded":
        fail(f"run ended {run['status']} ({run.get('detail')})")
    _, after = api("GET", f"/api/routines/{routine['id']}")
    if after["approval_mode"] != "allow_writes":
        fail(f"routine mode is {after['approval_mode']!r}")
    log("OK: allow_writes finished the run and switched the routine")


def cleanup(routines: list[str], threads: list[str]) -> None:
    for routine_id in routines:
        api("DELETE", f"/api/routines/{routine_id}")
    for thread_id in threads:
        api("DELETE", f"/api/threads/{thread_id}")
    for path in (NOTE, f"{NOTE}.second"):
        api("DELETE", f"/api/platform/files?path={urllib.parse.quote(path)}")


def main() -> None:
    routines: list[str] = []
    threads: list[str] = []
    try:
        check_allow_writes_from_a_run(routines, threads)
    finally:
        cleanup(routines, threads)
    log("PASS")


if __name__ == "__main__":
    main()

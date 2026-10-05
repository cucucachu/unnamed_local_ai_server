#!/usr/bin/env python3
"""M17-08 (GATE G17) scenario: routines run headlessly on the live stack.

Three routines due at the same minute, one to two minutes out (UTC):

- A, once, read-only: runs with nobody connected; its thread is listed
  under the routine and in the chats list with the model's answer.
- B, daily, disabled right after it's made: its grant is revoked and it
  never runs.
- C, daily, its grant revoked from Settings -> Sessions: it doesn't
  succeed and ends up disabled.

Env: E2E_AUTH_COOKIE, E2E_BASE (default http://localhost).
Deletes its routines and their run threads on the way out.
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime, timedelta
from typing import Any

BASE = os.environ.get("E2E_BASE", "http://localhost").rstrip("/")
COOKIE = os.environ.get("E2E_AUTH_COOKIE", "")
POLL_S = 30  # the scheduler's default ROUTINES_POLL_S
RUN_BUDGET_S = 420
MARKER = "G17-OK"


def log(msg: str) -> None:
    print(f"[g17] {time.strftime('%H:%M:%S')} {msg}", flush=True)


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


def expect(status: int, body: Any, want: int, what: str) -> Any:
    if status != want:
        fail(f"{what}: HTTP {status} {body!r}")
    return body


def create(name: str, schedule: dict, **extra: Any) -> dict:
    body = {
        "name": name,
        "prompt": f"Reply with exactly the text {MARKER} and nothing else.",
        "schedule": schedule,
        "timezone": "UTC",
        **extra,
    }
    return expect(*api("POST", "/api/routines", body), 201, f"create {name}")


def routine(routine_id: str) -> dict:
    return expect(*api("GET", f"/api/routines/{routine_id}"), 200, "get routine")


def runs(routine_id: str) -> list[dict]:
    return expect(*api("GET", f"/api/routines/{routine_id}/runs"), 200, "list runs")


def grant_sessions() -> dict[str, str]:
    """routine id -> its grant's session id, from Settings -> Sessions."""
    body = expect(*api("GET", "/api/platform/me/sessions"), 200, "list sessions")
    return {s["routine_id"]: s["id"] for s in body["sessions"] if s.get("routine_id")}


def main() -> None:
    if not COOKIE:
        sys.exit("E2E_AUTH_COOKIE is required (run through g17_routines_smoke.sh)")
    made: list[str] = []
    try:
        scenario(made)
    finally:
        for routine_id in made:
            for run in runs_quiet(routine_id):
                if run.get("thread_id"):
                    api("DELETE", f"/api/threads/{run['thread_id']}")
            api("DELETE", f"/api/routines/{routine_id}")
        if made:
            log(f"cleaned up {len(made)} routine(s) and their run threads")


def runs_quiet(routine_id: str) -> list[dict]:
    status, body = api("GET", f"/api/routines/{routine_id}/runs")
    return body if status == 200 and isinstance(body, list) else []


def scenario(made: list[str]) -> None:
    now = datetime.now(UTC)
    due = (now + timedelta(seconds=75)).replace(second=0, microsecond=0) + timedelta(minutes=1)
    at, hhmm = due.strftime("%Y-%m-%dT%H:%M"), due.strftime("%H:%M")
    log(f"all three due at {at}Z")

    a = create("e2e-g17 once", {"kind": "once", "at": at}, approval_mode="read_only")
    b = create("e2e-g17 disabled", {"kind": "daily", "time": hhmm})
    c = create("e2e-g17 revoked", {"kind": "daily", "time": hhmm})
    made.extend(r["id"] for r in (a, b, c))
    for r in (a, b, c):
        if not r["enabled"] or not r["next_run_at"].startswith(at):
            fail(f"{r['name']}: enabled={r['enabled']} next_run_at={r['next_run_at']}")

    grants = grant_sessions()
    missing = [r["name"] for r in (a, b, c) if r["id"] not in grants]
    if missing:
        fail(f"no routine grant in Settings -> Sessions for {missing}")
    log("OK: each routine holds a grant, listed in Settings -> Sessions")

    paused = expect(*api("PATCH", f"/api/routines/{b['id']}", {"enabled": False}), 200, "disable B")
    if paused["enabled"] or paused["next_run_at"] is not None:
        fail(f"disabled routine still scheduled: {paused}")
    if b["id"] in grant_sessions():
        fail("disabling B left its grant alive")
    log("OK: disabling B revoked its grant and cleared next_run_at")

    expect(*api("DELETE", f"/api/platform/me/sessions/{grants[c['id']]}"), 204, "revoke C")
    log("OK: revoked C's grant from Settings -> Sessions")

    deadline = time.monotonic() + (due - datetime.now(UTC)).total_seconds() + RUN_BUDGET_S
    log(f"waiting for A's run (scheduler polls every {POLL_S}s)...")
    while True:
        a_runs = runs(a["id"])
        if a_runs and a_runs[0]["status"] not in ("pending", "running"):
            break
        if time.monotonic() > deadline:
            fail(f"A didn't finish in time: {a_runs}")
        time.sleep(5)

    (run,) = a_runs
    if run["status"] != "succeeded":
        fail(f"A's run: {run}")
    thread_id = run["thread_id"]
    under = expect(*api("GET", f"/api/threads?routine_id={a['id']}"), 200, "threads by routine")
    if [t["id"] for t in under] != [thread_id]:
        fail(f"A's thread isn't listed under the routine: {under}")
    chats = expect(*api("GET", "/api/threads"), 200, "chats list")
    listed = [t for t in chats if t["id"] == thread_id]
    if not listed or listed[0].get("routine_id") != a["id"]:
        fail("A's run thread isn't in the chats list as a routine run")
    messages = expect(*api("GET", f"/api/threads/{thread_id}/messages"), 200, "messages")
    reply = (messages[-1].get("content") or "") if messages else ""
    if MARKER not in reply:
        fail(f"A's answer doesn't have {MARKER}: {reply[:200]!r}")
    after = routine(a["id"])
    if after["enabled"] or after["next_run_at"] is not None:
        fail(f"a one-shot should retire after its run: {after}")
    log(f"OK: A ran headlessly ({thread_id}), listed under the routine and in chats, then retired")

    # Both were due with A; one more poll for the scheduler to get to them.
    time.sleep(POLL_S + 10)
    if runs(b["id"]):
        fail(f"disabled B ran: {runs(b['id'])}")
    log("OK: disabled B never ran")
    c_runs, c_now = runs(c["id"]), routine(c["id"])
    if any(r["status"] == "succeeded" for r in c_runs):
        fail(f"C ran with a revoked grant: {c_runs}")
    if c_now["enabled"]:
        fail(f"C is still enabled after its grant was refused: {c_now}; runs {c_runs}")
    log(f"OK: C didn't run on a revoked grant and was disabled (runs: {[r['status'] for r in c_runs]})")
    log("PASS")


if __name__ == "__main__":
    main()

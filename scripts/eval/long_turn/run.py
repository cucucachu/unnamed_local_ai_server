#!/usr/bin/env python3
"""M16-02: one long real-model agent turn (web research + a file write).

Pass = the turn ends `completed`, the agent called `web_search` and
`web_fetch` at least once each, and the report file exists, is non-empty
and contains at least one https:// URL. Records steps, failed tool calls,
wall time and time to first token.

Env: E2E_AUTH_COOKIE, E2E_BASE (default http://localhost). Optional:
EVAL_RUNS (default 1), EVAL_TURN_TIMEOUT_S (default 2400), EVAL_OUT (default
this directory), EVAL_AGENT=candidate (route to agent-candidate).
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from websockets.asyncio.client import connect

HERE = Path(__file__).resolve().parent
BASE = os.environ.get("E2E_BASE", "http://localhost").rstrip("/")
COOKIE = os.environ.get("E2E_AUTH_COOKIE", "")
RUNS = int(os.environ.get("EVAL_RUNS", "1"))
TURN_TIMEOUT_S = int(os.environ.get("EVAL_TURN_TIMEOUT_S", "2400"))
AGENT_HEADERS = {"X-HomeAI-Agent": "candidate"} if os.environ.get("EVAL_AGENT") == "candidate" else {}

PROMPT = (
    "Use web search to find the three most recent stable Linux kernel releases and the date each was "
    "released. Open at least two of the sources to check them. Then write a short markdown report to "
    "{path} in my personal files with a table of version and release date, and a Sources section "
    "listing the URLs you used. Reply with just the file path when you're done."
)


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
        return e.code, e.read().decode("utf-8", "replace")


def download(path: str) -> tuple[int, bytes]:
    req = urllib.request.Request(
        f"{BASE}/api/platform/files/download?path={urllib.parse.quote(path)}",
        headers={"Cookie": COOKIE},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


async def turn(thread_id: str, prompt: str) -> tuple[list[dict], float | None, float]:
    ws_base = BASE.replace("http", "ws", 1)
    frames: list[dict] = []
    first_token: float | None = None
    start = time.monotonic()
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
                if first_token is None and frame.get("type") in ("token", "tool_start", "reasoning"):
                    first_token = time.monotonic() - start
                if frame["type"] == "approval_request":
                    await ws.send(json.dumps({"type": "cancel"}))
                if frame["type"] in ("turn_end", "error"):
                    break
    return frames, first_token, time.monotonic() - start


async def run_once(n: int) -> dict:
    path = f"/personal/research/kernels-{n}.md"
    result: dict[str, Any] = {"run": n, "pass": False, "failure": None}
    status, thread = api("POST", "/api/threads", {})
    if status != 201:
        result["failure"] = f"create_thread HTTP {status}"
        return result
    try:
        frames, ttft, wall = await turn(thread["id"], PROMPT.format(path=path))
    except TimeoutError:
        result["failure"] = "timeout"
        return result
    tools = [f for f in frames if f.get("type") == "tool_end"]
    names = [f.get("name") for f in tools]
    end = frames[-1] if frames else {}
    file_status, body = download(path)
    text = body.decode("utf-8", "replace") if file_status == 200 else ""
    result.update(
        {
            "wall_s": round(wall, 1),
            "first_event_s": None if ttft is None else round(ttft, 1),
            "end_status": end.get("status") if end.get("type") == "turn_end" else end.get("type"),
            "steps": len(tools),
            "tool_counts": {name: names.count(name) for name in sorted(set(filter(None, names)))},
            "failed_tools": [f.get("name") for f in tools if f.get("status") not in ("success", None)],
            "file_bytes": len(text),
            "reply": "".join(f.get("content") or "" for f in frames if f.get("type") == "token").strip()[:300],
        }
    )
    checks = {
        "completed": result["end_status"] == "completed",
        "searched": "web_search" in names,
        "fetched": "web_fetch" in names,
        "file_written": file_status == 200 and len(text.strip()) > 0,
        "has_source_url": "https://" in text,
    }
    result["checks"] = checks
    result["pass"] = all(checks.values())
    if not result["pass"]:
        result["failure"] = ",".join(k for k, ok in checks.items() if not ok)
    print(json.dumps(result), flush=True)
    return result


async def main() -> int:
    if not COOKIE:
        print("E2E_AUTH_COOKIE is required", file=sys.stderr)
        return 2
    results = [await run_once(n) for n in range(1, RUNS + 1)]
    report = {
        "agent": "candidate" if AGENT_HEADERS else "default",
        "passed": sum(r["pass"] for r in results),
        "runs": len(results),
        "results": results,
    }
    out = Path(os.environ.get("EVAL_OUT", str(HERE)))
    out.mkdir(parents=True, exist_ok=True)
    (out / "last-report.json").write_text(json.dumps(report, indent=2) + "\n")
    print(f"passed {report['passed']}/{report['runs']}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

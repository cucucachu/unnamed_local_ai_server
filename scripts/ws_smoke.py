#!/usr/bin/env -S uvx --from websockets python
"""Manual smoke test for `WS /ws/chat/{thread_id}` against the REAL running stack.

Not a pytest test — run by hand once caddy/agent-server/model-runner are up.
The chat socket requires a signed-in user and a thread that user owns
(M10-04), so pass a session cookie (`scripts/e2e/lib/auth.sh` exports one):

    E2E_AUTH_COOKIE='homeai_session=hs_...' \\
        uvx --from websockets python scripts/ws_smoke.py

Without `WS_SMOKE_THREAD_ID` a fresh thread is created via `POST
/api/threads` first. Thread id and prompt can be overridden via env vars so
other callers (e.g. `scripts/e2e/gate_m2.sh`, M2-07) can reuse this same WS
client against a different thread/prompt without duplicating the
connect/send/recv logic:

    WS_SMOKE_THREAD_ID=<uuid> WS_SMOKE_PROMPT="Reply with one short sentence." \\
        uvx --from websockets python scripts/ws_smoke.py

`E2E_BASE` (default `http://localhost`) is where Caddy is reached.
"""

import asyncio
import json
import os
import urllib.request

from websockets.asyncio.client import connect

BASE = os.environ.get("E2E_BASE", "http://localhost").rstrip("/")
COOKIE = os.environ.get("E2E_AUTH_COOKIE", "")
PROMPT = os.environ.get("WS_SMOKE_PROMPT", "Say exactly: PONG")


def create_thread() -> str:
    req = urllib.request.Request(
        f"{BASE}/api/threads",
        data=b"{}",
        method="POST",
        headers={"Content-Type": "application/json", "Cookie": COOKIE},
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read())["id"]


async def main() -> None:
    thread_id = os.environ.get("WS_SMOKE_THREAD_ID") or create_thread()
    ws_base = BASE.replace("http", "ws", 1)
    headers = {"Cookie": COOKIE} if COOKIE else {}
    async with connect(f"{ws_base}/ws/chat/{thread_id}", additional_headers=headers) as ws:
        await ws.send(json.dumps({"type": "user_message", "content": PROMPT}))
        async for raw in ws:
            frame = json.loads(raw)
            print(frame)
            if frame["type"] in ("turn_end", "error"):
                break


asyncio.run(main())

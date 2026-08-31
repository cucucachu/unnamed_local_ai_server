#!/usr/bin/env -S uvx --from websockets python
"""Manual smoke test for `WS /ws/chat/{thread_id}` against the REAL running stack.

Not a pytest test — run by hand once caddy/agent-server/model-runner are up:

    uvx --from websockets python scripts/ws_smoke.py

Thread id and prompt default to the original M2-04 smoke case (`smoke-1` /
"Say exactly: PONG") but can be overridden via env vars so other callers
(e.g. `scripts/e2e/gate_m2.sh`, M2-07) can reuse this same WS client against
a different thread/prompt without duplicating the connect/send/recv logic:

    WS_SMOKE_THREAD_ID=gate-m2 WS_SMOKE_PROMPT="Reply with one short sentence." \\
        uvx --from websockets python scripts/ws_smoke.py
"""

import asyncio
import json
import os

from websockets.asyncio.client import connect

THREAD_ID = os.environ.get("WS_SMOKE_THREAD_ID", "smoke-1")
PROMPT = os.environ.get("WS_SMOKE_PROMPT", "Say exactly: PONG")


async def main() -> None:
    async with connect(f"ws://localhost/ws/chat/{THREAD_ID}") as ws:
        await ws.send(json.dumps({"type": "user_message", "content": PROMPT}))
        async for raw in ws:
            frame = json.loads(raw)
            print(frame)
            if frame["type"] in ("turn_end", "error"):
                break


asyncio.run(main())

#!/usr/bin/env -S uvx --from websockets python
"""Manual smoke test for `WS /ws/chat/{thread_id}` against the REAL running stack.

Not a pytest test — run by hand once caddy/agent-server/model-runner are up:

    uvx --from websockets python scripts/ws_smoke.py
"""

import asyncio
import json

from websockets.asyncio.client import connect


async def main() -> None:
    async with connect("ws://localhost/ws/chat/smoke-1") as ws:
        await ws.send(json.dumps({"type": "user_message", "content": "Say exactly: PONG"}))
        async for raw in ws:
            frame = json.loads(raw)
            print(frame)
            if frame["type"] in ("turn_end", "error"):
                break


asyncio.run(main())

"""A WebSocket client that drives the ASGI app directly, on the test's own event loop.

httpx's `ASGITransport` has no WebSocket support, and Starlette's `TestClient`
runs the app on another loop (the Postgres pool belongs to the test's).
"""

from __future__ import annotations

import asyncio
import json
from typing import Any


class WsClosed(Exception):
    def __init__(self, code: int) -> None:
        super().__init__(code)
        self.code = code


class WsClient:
    def __init__(self, app, path: str, headers: dict[str, str]) -> None:
        self._to_app: asyncio.Queue = asyncio.Queue()
        self._from_app: asyncio.Queue = asyncio.Queue()
        scope = {
            "type": "websocket", "asgi": {"version": "3.0"}, "http_version": "1.1",
            "scheme": "ws", "path": path, "raw_path": path.encode(), "query_string": b"",
            "root_path": "", "server": ("platform", 8100), "client": ("127.0.0.1", 50000),
            "subprotocols": [],
            "headers": [(k.lower().encode(), v.encode()) for k, v in headers.items()],
        }  # fmt: skip
        self._task = asyncio.create_task(app(scope, self._to_app.get, self._from_app.put))

    async def _next(self, timeout: float) -> dict:
        get = asyncio.ensure_future(self._from_app.get())
        done, _ = await asyncio.wait({get, self._task}, timeout=timeout,
                                     return_when=asyncio.FIRST_COMPLETED)  # fmt: skip
        if get in done:
            return get.result()
        get.cancel()
        if self._task in done:
            self._task.result()
            raise WsClosed(1006)
        raise TimeoutError

    async def connect(self) -> None:
        await self._to_app.put({"type": "websocket.connect"})
        msg = await self._next(5)
        if msg["type"] == "websocket.close":
            raise WsClosed(msg.get("code", 1000))
        assert msg["type"] == "websocket.accept", msg

    async def recv(self, timeout: float = 5) -> Any:
        msg = await self._next(timeout)
        if msg["type"] == "websocket.close":
            raise WsClosed(msg.get("code", 1000))
        return json.loads(msg["text"])

    async def close(self) -> None:
        await self._to_app.put({"type": "websocket.disconnect", "code": 1000})
        try:
            await asyncio.wait_for(self._task, 5)
        except (TimeoutError, WsClosed):
            self._task.cancel()

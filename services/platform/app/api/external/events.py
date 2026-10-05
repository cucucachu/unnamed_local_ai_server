"""`/ws/platform/events`: change events for the caller's spaces.

Contract: docs/ARCHITECTURE.md §3 "App data" → "Events"; hub: `app.core.events`.
The socket is accepted first and then authenticated like any
`/api/platform/*` request (identity header from Caddy, or an agent's
delegation bearer), so a refusal can carry a close code: `4401` when the
credential is missing or invalid, or the session ends while it's open.
"""

import asyncio
import contextlib
import time

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.core import sessions, spaces
from app.core.errors import Unauthorized
from app.core.events import REFRESH_S, Subscriber
from app.core.principal import require_user

router = APIRouter()

WS_CLOSE_UNAUTHORIZED = 4401


async def _refresh(websocket: WebSocket, sub: Subscriber) -> bool:
    """Reload the subscriber's spaces; False once its session is no longer active."""
    async with websocket.app.state.db_pool.connection() as conn:
        session = await sessions.load_active(conn, sub.session_id, sub.user_id)
        if session is None:
            return False
        rows = await spaces.list_user_spaces(conn, sub.user_id, only=session["routine_space_id"])
        sub.spaces = frozenset(s["id"] for s in rows)
    return True


async def _until_closed(websocket: WebSocket) -> None:
    with contextlib.suppress(WebSocketDisconnect):
        while True:
            await websocket.receive_text()


@router.websocket("/ws/platform/events")
async def events(websocket: WebSocket) -> None:
    await websocket.accept()
    try:
        principal = await require_user(websocket)
    except Unauthorized:
        await websocket.close(code=WS_CLOSE_UNAUTHORIZED, reason="unauthenticated")
        return
    hub = websocket.app.state.events
    sub = Subscriber(principal.user_id, principal.session_id)
    await _refresh(websocket, sub)
    hub.add(sub)
    closed = asyncio.create_task(_until_closed(websocket))
    refreshed = time.monotonic()
    try:
        await websocket.send_json({"type": "ready"})
        while not closed.done():
            wait = max(0.0, refreshed + REFRESH_S - time.monotonic())
            waiting = asyncio.create_task(sub.next(wait))
            await asyncio.wait({waiting, closed}, return_when=asyncio.FIRST_COMPLETED)
            if closed.done():
                waiting.cancel()
                break
            if time.monotonic() >= refreshed + REFRESH_S:
                if not await _refresh(websocket, sub):
                    await websocket.close(code=WS_CLOSE_UNAUTHORIZED, reason="unauthenticated")
                    break
                refreshed = time.monotonic()
            for event in waiting.result():
                await websocket.send_json(event)
    except WebSocketDisconnect:
        pass
    finally:
        hub.remove(sub)
        closed.cancel()

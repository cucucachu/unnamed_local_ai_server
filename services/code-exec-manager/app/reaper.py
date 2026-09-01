"""The idle-reaper background task loop - M4-03.

Deliberately thin: the actual reap-once logic (listing `homeai.exec=1`
containers, adopting ones this process doesn't recognize, stopping/removing
idle ones) lives on `SessionManager.reap_once` in `app/sessions.py`, which
already owns `_last_used` and the docker client and is what
`tests/test_reaper_unit.py` exercises directly with an injectable clock -
this module is only the `while True: ...; await asyncio.sleep(...)` wiring,
started/cancelled from `app/main.py`'s lifespan.
"""

from __future__ import annotations

import asyncio
import logging

from app.sessions import SessionManager

logger = logging.getLogger(__name__)

REAP_INTERVAL_S = 60


async def reap_loop(session_manager: SessionManager) -> None:
    """Runs `session_manager.reap_once()` every `REAP_INTERVAL_S` seconds,
    forever, until the task running this coroutine is cancelled (by
    `app/main.py`'s lifespan on shutdown).

    `reap_once` already catches and logs its own internal errors (a listing
    failure, or a single container's own processing failure) rather than
    raising - the `try`/`except` here is a defensive backstop only, so that
    even a bug in `reap_once` itself can never kill this loop permanently.
    """
    while True:
        try:
            await session_manager.reap_once()
        except Exception:
            logger.exception("reaper: unhandled error in reap pass")
        await asyncio.sleep(REAP_INTERVAL_S)

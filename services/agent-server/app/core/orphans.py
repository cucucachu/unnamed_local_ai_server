"""Handing pre-Stage-3 data to the bootstrap admin (docs/PLATFORM.md §5 "Legacy migration").

Threads created before M10-04 have no owner, and the old global settings
document belongs to nobody. Both go to the platform's bootstrap admin, whose
id comes from `GET /internal/bootstrap-admin` (service auth with
`PLATFORM_AGENT_TOKEN`). Until setup is completed there is no such admin
(`404`): the data stays invisible to everyone, and `adopt` retries on a
later call (throttled). Once it has succeeded, it's a no-op for the life of
the process; afterwards nothing can create ownerless threads.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable

import httpx

from app.db.settings import SettingsStore
from app.db.threads import ThreadStore

logger = logging.getLogger(__name__)

AdminLookup = Callable[[], Awaitable[str | None]]


def http_admin_lookup(platform_url: str, agent_token: str, timeout_s: float = 3.0) -> AdminLookup:
    """`None` while bootstrap is pending; raises on any other failure."""

    async def lookup() -> str | None:
        async with httpx.AsyncClient(timeout=timeout_s) as client:
            response = await client.get(
                f"{platform_url}/internal/bootstrap-admin",
                headers={"Authorization": f"Bearer {agent_token}"},
            )
        if response.status_code == 404:
            return None
        response.raise_for_status()
        return str(response.json()["user_id"])

    return lookup


class OrphanAdopter:
    def __init__(
        self,
        lookup: AdminLookup | None,
        thread_store: ThreadStore,
        settings_store: SettingsStore,
        *,
        retry_interval_s: float = 10.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._lookup = lookup
        self._thread_store = thread_store
        self._settings_store = settings_store
        self._retry_interval_s = retry_interval_s
        self._clock = clock
        self._attempted_at: float | None = None
        self._lock = asyncio.Lock()
        self.done = lookup is None

    async def adopt(self) -> None:
        if self.done:
            return
        async with self._lock:
            now = self._clock()
            if self.done or (
                self._attempted_at is not None and now - self._attempted_at < self._retry_interval_s
            ):
                return
            self._attempted_at = now
            try:
                admin_id = await self._lookup()
            except Exception as exc:  # noqa: BLE001 - retried on a later call
                logger.warning("orphans: bootstrap admin lookup failed: %s", exc)
                return
            if admin_id is None:
                return
            threads = await self._thread_store.adopt_orphans(admin_id)
            settings = await self._settings_store.adopt_legacy(admin_id)
            self.done = True
            logger.info(
                "orphans: gave %d thread(s) and %d legacy setting(s) to bootstrap admin %s",
                threads, settings, admin_id,
            )  # fmt: skip

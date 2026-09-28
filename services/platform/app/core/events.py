"""Change events for `/ws/platform/events` (docs/PLATFORM.md §7 "Runtime and bridge").

In-process (the platform runs one worker): `EventHub.publish` hands an
event to every open socket whose user is a member of the space it concerns.
A subscriber's spaces are reloaded every `REFRESH_S` (and its session
re-checked then), so a membership change reaches an open socket within that
window. Events are coalesced per key while a socket hasn't taken them yet:
ten writes in a burst are one `db_changed` for a slow reader, and a
subscriber's backlog never grows past one event per key.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterable
from uuid import UUID

REFRESH_S = 10.0


class Subscriber:
    def __init__(self, user_id: UUID, session_id: UUID) -> None:
        self.user_id = user_id
        self.session_id = session_id
        self.spaces: frozenset[UUID] = frozenset()
        self._pending: dict[tuple, dict] = {}
        self._wake = asyncio.Event()

    def offer(self, key: tuple, event: dict) -> None:
        self._pending.pop(key, None)
        self._pending[key] = event
        self._wake.set()

    async def next(self, timeout: float) -> list[dict]:
        """The pending events, oldest first; `[]` after `timeout` with nothing new."""
        try:
            await asyncio.wait_for(self._wake.wait(), timeout)
        except TimeoutError:
            return []
        self._wake.clear()
        events, self._pending = list(self._pending.values()), {}
        return events


class EventHub:
    def __init__(self) -> None:
        self._subscribers: set[Subscriber] = set()

    def add(self, sub: Subscriber) -> None:
        self._subscribers.add(sub)

    def remove(self, sub: Subscriber) -> None:
        self._subscribers.discard(sub)

    def publish(self, key: tuple, event: dict, space_ids: Iterable[UUID]) -> None:
        wanted = set(space_ids)
        for sub in self._subscribers:
            if sub.spaces & wanted:
                sub.offer(key, event)

    def db_changed(self, space_id: UUID, instance_id: UUID) -> None:
        event = {"type": "db_changed", "instance_id": str(instance_id)}
        self.publish(("db_changed", instance_id), event, [space_id])

    def app_built(self, space_ids: Iterable[UUID], app_id: UUID, version: str) -> None:
        """For the builder (M12-04): `space_ids` are the spaces that can see the app."""
        event = {"type": "app_built", "app_id": str(app_id), "version": version}
        self.publish(("app_built", app_id), event, space_ids)

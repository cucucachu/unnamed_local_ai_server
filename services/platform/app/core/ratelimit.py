"""In-memory sliding-window rate limiter for the credential endpoints.

State lives in the process, which is why the Dockerfile pins uvicorn to a
single worker. Callers `acquire` a slot *before* checking a credential and
`release` it when the credential turns out valid, so only failures count
but a burst of parallel guesses can't all slip in before the first failure
is recorded.
"""

from __future__ import annotations

import math
import time
from collections import deque
from collections.abc import Callable, Iterable

# Drop idle keys once the table gets this big, so spraying usernames can't
# grow memory without bound.
_PRUNE_AT = 10_000


class RateLimited(Exception):
    def __init__(self, retry_after_s: int) -> None:
        super().__init__("rate_limited")
        self.retry_after_s = retry_after_s


class RateLimiter:
    def __init__(
        self,
        limit: int,
        window_s: float = 60.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.limit = limit
        self.window_s = window_s
        self._clock = clock
        self._hits: dict[str, deque[float]] = {}

    def _live(self, key: str, now: float) -> deque[float]:
        hits = self._hits.setdefault(key, deque())
        while hits and hits[0] <= now - self.window_s:
            hits.popleft()
        return hits

    def acquire(self, keys: Iterable[str]) -> list[str]:
        """Record one attempt against every key, or raise `RateLimited` recording none."""
        keys = list(keys)
        now = self._clock()
        if len(self._hits) > _PRUNE_AT:
            for key in [k for k, v in self._hits.items() if not v or v[-1] <= now - self.window_s]:
                del self._hits[key]
        buckets = [self._live(key, now) for key in keys]
        full = [b for b in buckets if len(b) >= self.limit]
        if full:
            oldest = min(b[0] for b in full)
            raise RateLimited(max(1, math.ceil(oldest + self.window_s - now)))
        for bucket in buckets:
            bucket.append(now)
        return keys

    def release(self, keys: Iterable[str]) -> None:
        """Give back the attempt `acquire` recorded (the credential was valid)."""
        for key in keys:
            bucket = self._hits.get(key)
            if bucket:
                bucket.pop()

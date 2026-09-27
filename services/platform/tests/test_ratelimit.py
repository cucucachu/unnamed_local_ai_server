import pytest

from app.core.ratelimit import RateLimited, RateLimiter


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def clock() -> Clock:
    return Clock()


def test_limit_then_window_slides(clock):
    limiter = RateLimiter(3, 60, clock=clock)
    for _ in range(3):
        limiter.acquire(["a"])
        clock.now += 1
    with pytest.raises(RateLimited) as excinfo:
        limiter.acquire(["a"])
    assert excinfo.value.retry_after_s == 57
    clock.now = 1060.0
    limiter.acquire(["a"])


def test_any_full_key_blocks_and_records_nothing(clock):
    limiter = RateLimiter(2, 60, clock=clock)
    limiter.acquire(["user:a", "ip:1"])
    limiter.acquire(["user:b", "ip:1"])
    with pytest.raises(RateLimited):
        limiter.acquire(["user:c", "ip:1"])
    # The refused attempt didn't count against user:c.
    limiter.acquire(["user:c", "ip:2"])
    limiter.acquire(["user:c", "ip:3"])
    with pytest.raises(RateLimited):
        limiter.acquire(["user:c", "ip:4"])


def test_release_refunds(clock):
    limiter = RateLimiter(1, 60, clock=clock)
    for _ in range(5):
        limiter.release(limiter.acquire(["a"]))
    limiter.acquire(["a"])
    with pytest.raises(RateLimited):
        limiter.acquire(["a"])


def test_idle_keys_pruned(clock, monkeypatch):
    monkeypatch.setattr("app.core.ratelimit._PRUNE_AT", 10)
    limiter = RateLimiter(5, 60, clock=clock)
    for i in range(20):
        limiter.acquire([f"k{i}"])
    clock.now += 61
    limiter.acquire(["fresh"])
    assert set(limiter._hits) == {"fresh"}

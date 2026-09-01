"""Unit tests for `SessionManager.reap_once` (M4-03's idle reaper) against
`FakeDockerClient` - no real Docker daemon required, no real sleeping (the
idle clock is driven entirely via `reap_once`'s injectable `now`).

`app/reaper.py`'s own `reap_loop` is intentionally untested here: it's a
1-line `while True: await reap_once(); await asyncio.sleep(60)` wrapper with
no branching logic of its own - all the actual behavior under test lives on
`SessionManager.reap_once`, which this file calls directly.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.core.config import Settings
from app.sessions import SessionManager, container_name
from tests.fake_docker import FakeDockerClient


async def test_reap_once_removes_idle_container(
    manager: SessionManager, fake_docker: FakeDockerClient, test_settings: Settings
) -> None:
    await manager.ensure("sess-1")
    container = fake_docker.containers.get(container_name("sess-1"))

    now = datetime.now(UTC) + timedelta(minutes=test_settings.exec_idle_minutes + 1)
    await manager.reap_once(now=now)

    assert container.stopped is True
    assert container.removed is True
    assert "sess-1" not in manager._last_used


async def test_reap_once_keeps_active_container(
    manager: SessionManager, fake_docker: FakeDockerClient
) -> None:
    await manager.ensure("sess-1")
    container = fake_docker.containers.get(container_name("sess-1"))

    now = datetime.now(UTC)  # freshly `_touch`ed by `ensure` - well within the idle window
    await manager.reap_once(now=now)

    assert container.removed is False
    assert "sess-1" in manager._last_used


async def test_reap_once_adopts_unknown_container_then_reaps_once_idle(
    manager: SessionManager, fake_docker: FakeDockerClient, test_settings: Settings
) -> None:
    await manager.ensure("sess-1")
    container = fake_docker.containers.get(container_name("sess-1"))

    # Simulate a manager restart: the in-memory `_last_used` is gone, but
    # the container (and Docker's own record of when it started) survives.
    manager._last_used.pop("sess-1")
    started_at = datetime(2024, 1, 1, tzinfo=UTC)
    container.attrs["State"]["StartedAt"] = started_at.isoformat()

    # First pass, still within the idle window relative to `started_at`:
    # adopted (seeded from `StartedAt`), not reaped.
    still_idle_but_within_window = started_at + timedelta(minutes=test_settings.exec_idle_minutes - 1)
    await manager.reap_once(now=still_idle_but_within_window)

    assert container.removed is False
    assert manager._last_used["sess-1"] == started_at

    # Second, LATER pass: now idle past the threshold relative to the
    # ADOPTED clock (not reset by the adoption itself) - gets reaped.
    past_the_window = started_at + timedelta(minutes=test_settings.exec_idle_minutes + 1)
    await manager.reap_once(now=past_the_window)

    assert container.stopped is True
    assert container.removed is True
    assert "sess-1" not in manager._last_used


async def test_reap_once_swallows_listing_errors(
    manager: SessionManager, fake_docker: FakeDockerClient
) -> None:
    fake_docker.containers.list_error = RuntimeError("boom: docker engine api unavailable")

    await manager.reap_once()  # must not raise


async def test_reap_once_isolates_a_single_containers_processing_error(
    manager: SessionManager, fake_docker: FakeDockerClient, test_settings: Settings
) -> None:
    """A single container's own processing blowing up (e.g. a malformed/
    missing `attrs` on an "unknown" container mid-adoption) mustn't stop the
    reaper from still reaping the others in the same pass - `reap_once`
    catches per-container, not just per-listing.
    """
    await manager.ensure("sess-broken")
    await manager.ensure("sess-idle")
    broken = fake_docker.containers.get(container_name("sess-broken"))
    idle = fake_docker.containers.get(container_name("sess-idle"))

    # Force "sess-broken" onto the adoption path, then break the very
    # `attrs` lookup `_adopt` depends on.
    manager._last_used.pop("sess-broken")
    broken.attrs = None  # type: ignore[assignment]  # `.get()` on this raises AttributeError

    now = datetime.now(UTC) + timedelta(minutes=test_settings.exec_idle_minutes + 1)
    await manager.reap_once(now=now)

    assert idle.stopped is True
    assert idle.removed is True
    assert broken.removed is False

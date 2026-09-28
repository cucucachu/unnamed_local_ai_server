"""`app.core.legacy`: the flagged-off move of pre-Stage-3 files into the admin's personal space."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from app.core import legacy
from app.main import create_app
from tests.conftest import make_settings, running
from tests.helpers import bootstrap_admin, create_user, sql


def _legacy_tree(root: Path) -> Path:
    (root / "notes").mkdir(parents=True)
    (root / "notes" / "a.md").write_text("# a")
    (root / "photo.jpg").write_bytes(b"jpg")
    (root / ".hidden").write_text("dot")
    (root / "link").symlink_to("notes/a.md")
    return root


def _app(pg_database, data_dir: Path, legacy_dir: Path, enabled: bool = True):
    return create_app(
        make_settings(
            pg_database,
            data_dir,
            platform_migrate_legacy_files=enabled,
            platform_legacy_files_dir=legacy_dir,
        )
    )


def _admin_home(platform) -> tuple[Path, dict, dict]:
    (admin,) = sql(platform, "SELECT id, uid FROM users WHERE role = 'admin'")
    (space,) = sql(
        platform,
        "SELECT id, gid FROM spaces WHERE kind = 'personal' AND owner_user_id = %s",
        (admin["id"],),
    )
    home = platform.app.state.storage.space_dir(space["id"]).resolve() / "files"
    return home, admin, space


class _P:
    """Enough of `tests.conftest.Platform` for the helpers."""

    def __init__(self, app, client, db, data_dir):
        self.app, self.client, self.db, self.data_dir = app, client, db, data_dir


@pytest.fixture
def legacy_dir(tmp_path) -> Path:
    return _legacy_tree(tmp_path / "legacy-files")


async def test_off_by_default(pg_database, tmp_path, legacy_dir) -> None:
    data = tmp_path / "data"
    app = _app(pg_database, data, legacy_dir, enabled=False)
    async with running(app) as client:
        p = _P(app, client, pg_database, data)
        await bootstrap_admin(p)
        home, _, _ = _admin_home(p)
        assert list(home.iterdir()) == []
    assert (legacy_dir / "photo.jpg").exists()
    assert not (data / legacy.MARKER_FILENAME).exists()


async def test_waits_for_bootstrap_then_runs_after_setup(
    pg_database, tmp_path, legacy_dir, chowns
) -> None:
    data = tmp_path / "data"
    app = _app(pg_database, data, legacy_dir)
    async with running(app) as client:
        p = _P(app, client, pg_database, data)
        # startup ran before any admin existed: nothing moved, no marker
        assert (legacy_dir / "photo.jpg").exists()
        assert not (data / legacy.MARKER_FILENAME).exists()
        await create_user(p, "early")  # a non-admin user doesn't trigger it
        await bootstrap_admin(p)
        home, admin, space = _admin_home(p)

    assert sorted(x.name for x in home.iterdir()) == [".hidden", "link", "notes", "photo.jpg"]
    assert list(legacy_dir.iterdir()) == []
    assert (home / "notes" / "a.md").read_text() == "# a"
    assert (home / "link").is_symlink()
    owner = (admin["uid"], space["gid"])
    for rel in ("notes", "notes/a.md", "photo.jpg", ".hidden", "link"):
        assert chowns[home / rel] == owner, rel
    marker = json.loads((data / legacy.MARKER_FILENAME).read_text())
    assert (marker["admin_id"], marker["space_id"], marker["entries"]) == (
        str(admin["id"]), str(space["id"]), 4,
    )  # fmt: skip


async def test_idempotent_across_restarts(pg_database, tmp_path, legacy_dir) -> None:
    data = tmp_path / "data"
    app = _app(pg_database, data, legacy_dir)
    async with running(app) as client:
        p = _P(app, client, pg_database, data)
        await bootstrap_admin(p)
        home, _, _ = _admin_home(p)
    marker = (data / legacy.MARKER_FILENAME).read_text()
    before = sorted(x.name for x in home.iterdir())

    # Something reappears in the legacy root; restarts must not touch it.
    (legacy_dir / "later.txt").write_text("new")
    for _ in range(2):
        app = _app(pg_database, data, legacy_dir)
        async with running(app):
            pass
    assert sorted(x.name for x in home.iterdir()) == before
    assert (legacy_dir / "later.txt").exists()
    assert (data / legacy.MARKER_FILENAME).read_text() == marker


async def test_admin_already_bootstrapped_at_startup(pg_database, tmp_path, legacy_dir) -> None:
    data = tmp_path / "data"
    app = _app(pg_database, data, legacy_dir, enabled=False)
    async with running(app) as client:
        await bootstrap_admin(_P(app, client, pg_database, data))
    app = _app(pg_database, data, legacy_dir)
    async with running(app) as client:
        home, _, _ = _admin_home(_P(app, client, pg_database, data))
        assert (home / "photo.jpg").exists()


async def test_name_collisions_are_renamed(pg_database, tmp_path, legacy_dir) -> None:
    data = tmp_path / "data"
    app = _app(pg_database, data, legacy_dir, enabled=False)
    async with running(app) as client:
        p = _P(app, client, pg_database, data)
        await bootstrap_admin(p)
        home, _, _ = _admin_home(p)
        (home / "photo.jpg").write_bytes(b"mine")
        (home / "photo (migrated).jpg").write_bytes(b"also mine")
        (home / "notes").mkdir()
        (home / ".hidden").write_text("mine")
    app = _app(pg_database, data, legacy_dir)
    async with running(app):
        pass
    assert (home / "photo.jpg").read_bytes() == b"mine"
    assert (home / "photo (migrated 2).jpg").read_bytes() == b"jpg"
    assert (home / "notes (migrated)" / "a.md").read_text() == "# a"
    assert (home / ".hidden (migrated)").read_text() == "dot"


async def test_interrupted_run_resumes(pg_database, tmp_path, legacy_dir, monkeypatch) -> None:
    data = tmp_path / "data"
    app = _app(pg_database, data, legacy_dir, enabled=False)
    async with running(app) as client:
        p = _P(app, client, pg_database, data)
        await bootstrap_admin(p)
        home, _, _ = _admin_home(p)

    real_move = legacy._move_entry
    calls = []

    def flaky_move(*args):
        calls.append(args)
        if len(calls) == 3:
            raise OSError("disk full")
        return real_move(*args)

    monkeypatch.setattr(legacy, "_move_entry", flaky_move)
    app = _app(pg_database, data, legacy_dir)
    async with running(app):  # failure is logged, startup continues
        pass
    assert not (data / legacy.MARKER_FILENAME).exists()
    assert len(list(home.iterdir())) == 2

    monkeypatch.setattr(legacy, "_move_entry", real_move)
    app = _app(pg_database, data, legacy_dir)
    async with running(app):
        pass
    assert sorted(x.name for x in home.iterdir()) == [".hidden", "link", "notes", "photo.jpg"]
    assert (data / legacy.MARKER_FILENAME).exists()


async def test_missing_legacy_dir_is_a_no_op(pg_database, tmp_path) -> None:
    data = tmp_path / "data"
    app = _app(pg_database, data, tmp_path / "does-not-exist")
    async with running(app) as client:
        await bootstrap_admin(_P(app, client, pg_database, data))
    assert not (data / legacy.MARKER_FILENAME).exists()


def test_free_name(tmp_path) -> None:
    (tmp_path / "a.tar.gz").write_text("")
    (tmp_path / "Makefile").write_text("")
    fd = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        assert legacy._free_name(fd, "a.tar.gz") == "a (migrated).tar.gz"
        assert legacy._free_name(fd, "Makefile") == "Makefile (migrated)"
        assert legacy._free_name(fd, "new.txt") == "new.txt"
    finally:
        os.close(fd)

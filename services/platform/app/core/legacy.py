"""Pre-Stage-3 files -> the bootstrap admin's personal space (docs/PLATFORM.md §5 "Legacy migration").

Off unless `PLATFORM_MIGRATE_LEGACY_FILES` is set (compose sets it since
M11-02, when the agent's file tools moved onto the platform).

When on, it runs at startup and right after setup completes, and only once
`bootstrap_admin_id` exists. Each top-level entry of the legacy root
(`FILES_DIR`, mounted at `/data/legacy-files`) is moved into the admin's
personal `files/` - renamed `name (migrated).ext`, `name (migrated 2).ext`,
... if that name is taken - then handed to the admin's uid and the space's
gid. A marker in the platform data volume records completion; an
interrupted run leaves the rest in the legacy root and continues next time.

Both trees were writable by exec containers, so entries are moved by name
between directory fds (`renameat`, or an fd-walking copy and delete across
mounts) and adopted without following links (`app.core.fsops`).
"""

from __future__ import annotations

import errno
import json
import logging
import os
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import anyio.to_thread
from psycopg import AsyncConnection

from app.core import fsops, spaces, vfs
from app.core.beneath import Root
from app.core.bootstrap import bootstrap_admin_id
from app.core.storage import SpaceStorage

logger = logging.getLogger(__name__)

MARKER_FILENAME = "legacy-files-migrated.json"


def _exists(dir_fd: int, name: str) -> bool:
    try:
        os.stat(name, dir_fd=dir_fd, follow_symlinks=False)
    except FileNotFoundError:
        return False
    return True


def _free_name(dir_fd: int, name: str) -> str:
    if not _exists(dir_fd, name):
        return name
    stem, dot, ext = name.partition(".") if not name.startswith(".") else (name, "", "")
    for n in range(1, 10_000):
        tag = " (migrated)" if n == 1 else f" (migrated {n})"
        candidate = f"{stem}{tag}{dot}{ext}"
        if not _exists(dir_fd, candidate):
            return candidate
    raise FileExistsError(f"{name}: no free name")


def _move_entry(src_dir: int, name: str, dst_dir: int, target: str, owner: fsops.Owner) -> None:
    try:
        os.rename(name, target, src_dir_fd=src_dir, dst_dir_fd=dst_dir)
    except OSError as exc:
        if exc.errno != errno.EXDEV:
            raise
        fsops.copy_at(src_dir, name, dst_dir, target, owner)
        fsops.remove_at(src_dir, name)


def move_entries(src_dir: Path, dst: Root, uid: int, gid: int) -> int:
    """Move every entry of `src_dir` into `dst` (across mounts too) and adopt it."""
    moved = 0
    src_fd = os.open(src_dir, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        for name in sorted(os.listdir(src_fd)):
            target = _free_name(dst.fd, name)
            if target != name:
                logger.warning("legacy files: %s exists, moving to %s", name, target)
            _move_entry(src_fd, name, dst.fd, target, fsops.Owner(uid, gid))
            fsops.adopt_at(dst.fd, target, gid, uid)
            moved += 1
    finally:
        os.close(src_fd)
    return moved


async def migrate_legacy_files(
    conn: AsyncConnection, storage: SpaceStorage, legacy_dir: Path, data_dir: Path
) -> int | None:
    """Entries moved, or None if there was nothing to do yet (or ever)."""
    marker = data_dir / MARKER_FILENAME
    if marker.exists():
        logger.info("legacy files: already migrated (%s)", marker)
        return None
    admin_id = await bootstrap_admin_id(conn)
    if admin_id is None:
        logger.info("legacy files: waiting for bootstrap to complete")
        return None
    if not legacy_dir.is_dir():
        logger.warning("legacy files: %s is not a directory; nothing to migrate", legacy_dir)
        return None

    cur = await conn.execute("SELECT username, uid FROM users WHERE id = %s", (UUID(admin_id),))
    admin = await cur.fetchone()
    space = await spaces.get_personal_space(conn, UUID(admin_id))
    with vfs.open_files_root(storage, space["id"]) as root:
        moved = await anyio.to_thread.run_sync(
            move_entries, legacy_dir, root, admin["uid"], space["gid"]
        )

    marker.write_text(json.dumps({
        "admin_id": admin_id, "space_id": str(space["id"]), "entries": moved,
        "migrated_at": datetime.now(UTC).isoformat(),
    }) + "\n")  # fmt: skip
    logger.info(
        "legacy files: moved %d entries from %s into %s's personal space",
        moved, legacy_dir, admin["username"],
    )  # fmt: skip
    return moved


async def maybe_migrate(app) -> None:
    """Run the migration if it's enabled; a failure is logged and retried on the next start."""
    s = app.state.settings
    if not s.platform_migrate_legacy_files:
        return
    try:
        async with app.state.db_pool.connection() as conn:
            await migrate_legacy_files(
                conn, app.state.storage, s.platform_legacy_files_dir, s.platform_data_dir
            )
    except Exception:
        logger.exception("legacy files: migration failed; will retry on the next start")

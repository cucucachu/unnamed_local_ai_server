"""Pre-Stage-3 files -> the bootstrap admin's personal space (docs/PLATFORM.md §5 "Legacy migration").

Off unless `PLATFORM_MIGRATE_LEGACY_FILES` is set: until M11-02 switches the
agent's file tools to the platform, the agent still works in `FILES_DIR`, so
moving its contents early would leave it looking at an empty directory.

When on, it runs at startup and right after setup completes, and only once
`bootstrap_admin_id` exists. Each top-level entry of the legacy root
(`FILES_DIR`, mounted at `/data/legacy-files`) is moved into the admin's
personal `files/` - renamed `name (migrated).ext`, `name (migrated 2).ext`,
... if that name is taken - then handed to the admin's uid and the space's
gid. A marker in the platform data volume records completion; an
interrupted run leaves the rest in the legacy root and continues next time.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import anyio.to_thread
from psycopg import AsyncConnection

from app.core import fsops, spaces, vfs
from app.core.bootstrap import bootstrap_admin_id
from app.core.storage import SpaceStorage

logger = logging.getLogger(__name__)

MARKER_FILENAME = "legacy-files-migrated.json"


def _free_name(dst_dir: Path, name: str) -> Path:
    if not os.path.lexists(dst_dir / name):
        return dst_dir / name
    stem, dot, ext = name.partition(".") if not name.startswith(".") else (name, "", "")
    for n in range(1, 10_000):
        tag = " (migrated)" if n == 1 else f" (migrated {n})"
        candidate = dst_dir / f"{stem}{tag}{dot}{ext}"
        if not os.path.lexists(candidate):
            return candidate
    raise FileExistsError(f"{name}: no free name in {dst_dir}")


def move_entries(src_dir: Path, dst_dir: Path, uid: int, gid: int) -> int:
    """Move every entry of `src_dir` into `dst_dir` (across mounts too) and adopt it."""
    moved = 0
    for entry in sorted(os.scandir(src_dir), key=lambda e: e.name):
        target = _free_name(dst_dir, entry.name)
        if target.name != entry.name:
            logger.warning("legacy files: %s exists, moving to %s", entry.name, target.name)
        shutil.move(entry.path, target)
        fsops.adopt_tree(target, gid, uid)
        moved += 1
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
    root = vfs.files_root(storage, space["id"])
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

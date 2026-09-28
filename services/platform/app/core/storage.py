"""Per-space directory trees under `SPACES_DIR` (docs/PLATFORM.md §5 "Host layout").

    <root>/<space_id>/          root:<gid> 2770
    <root>/<space_id>/files/    root:<gid> 2770
    <root>/<space_id>/apps/     root:<gid> 2750
    <root>/<space_id>/apps/<instance_id>/                 root:<gid> 2750
    <root>/<space_id>/apps/<instance_id>/{ro,snapshots}/  root:<gid> 2750
    <root>/<space_id>/apps/.trash/<instance_id>-<stamp>/  an uninstalled instance, kept

`<root>` itself is root-owned and not group-writable, so `<space_id>` can be
trusted; below it `files/` is writable by the space's group (exec
containers), so each child is opened relative to its parent's fd with
`O_NOFOLLOW` and fixed up via `fchown`/`fchmod` - a swapped-in symlink makes
`ensure` fail instead of redirecting a root-privileged chown.

`apps/` and everything below it is written only by the platform (D12), so
it isn't group-writable: SQLite reopens an instance's database and its
`-wal`/`-shm` by path, which is only safe when nobody else can create or
swap a name on that path. `ro/` is what exec may see (read-only).

Standard library only: `tests/test_storage.py` runs this module as root in
a bare `python` container to check real ownership on disk.
"""

from __future__ import annotations

import logging
import os
import stat
from collections.abc import Iterable
from pathlib import Path
from uuid import UUID

logger = logging.getLogger(__name__)

OWNER_UID = 0
DIR_MODE = 0o2770
APPS_MODE = 0o2750
SUBDIRS = ("files", "apps")
SUBDIR_MODES = {"files": DIR_MODE, "apps": APPS_MODE}
INSTANCE_SUBDIRS = ("ro", "snapshots")
TRASH_DIR = ".trash"

_OPEN_DIR = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC


class StorageError(Exception):
    pass


def _fchown(fd: int, uid: int, gid: int) -> None:
    os.fchown(fd, uid, gid)


def _ensure_dir(name: str, gid: int, *, dir_fd: int | None = None, mode: int = DIR_MODE) -> int:
    """Create `name` if missing, fix owner/mode, and return an open fd on it."""
    try:
        os.mkdir(name, 0o700, dir_fd=dir_fd)
    except FileExistsError:
        pass
    try:
        fd = os.open(name, _OPEN_DIR, dir_fd=dir_fd)
    except OSError as exc:
        raise StorageError(f"{name}: not a plain directory ({exc.strerror})") from exc
    try:
        st = os.fstat(fd)
        if (st.st_uid, st.st_gid) != (OWNER_UID, gid):
            _fchown(fd, OWNER_UID, gid)
        # chown can clear setgid, so check the mode after it.
        if stat.S_IMODE(os.fstat(fd).st_mode) != mode:
            os.fchmod(fd, mode)
    except BaseException:
        os.close(fd)
        raise
    return fd


class SpaceStorage:
    def __init__(self, root: Path) -> None:
        self.root = root

    def space_dir(self, space_id: UUID) -> Path:
        return self.root / str(space_id)

    def ensure(self, space_id: UUID, gid: int) -> None:
        """Idempotently create the space's tree with the right owner, group, and mode."""
        if not self.root.is_dir():
            raise StorageError(f"{self.root}: spaces root is not a directory")
        space_fd = _ensure_dir(str(self.space_dir(space_id)), gid)
        try:
            for sub in SUBDIRS:
                os.close(_ensure_dir(sub, gid, dir_fd=space_fd, mode=SUBDIR_MODES[sub]))
        finally:
            os.close(space_fd)

    def _open_apps(self, space_id: UUID, gid: int) -> int:
        space_fd = _ensure_dir(str(self.space_dir(space_id)), gid)
        try:
            return _ensure_dir("apps", gid, dir_fd=space_fd, mode=APPS_MODE)
        finally:
            os.close(space_fd)

    def instance_dir(self, space_id: UUID, instance_id: UUID) -> Path:
        return self.space_dir(space_id) / "apps" / str(instance_id)

    def ensure_instance(self, space_id: UUID, gid: int, instance_id: UUID) -> Path:
        """`apps/<instance_id>/` and its `ro/` and `snapshots/`, root:<gid> 2750."""
        os.close(self.open_instance(space_id, gid, instance_id))
        return self.instance_dir(space_id, instance_id)

    def open_instance(
        self, space_id: UUID, gid: int, instance_id: UUID, *, create: bool = True
    ) -> int:
        """`ensure_instance`, returning an open fd on `apps/<instance_id>/` (the caller closes it).

        Without `create`, a missing instance dir is `FileNotFoundError`.
        """
        apps_fd = self._open_apps(space_id, gid)
        try:
            if not create:
                os.stat(str(instance_id), dir_fd=apps_fd, follow_symlinks=False)
            fd = _ensure_dir(str(instance_id), gid, dir_fd=apps_fd, mode=APPS_MODE)
        finally:
            os.close(apps_fd)
        try:
            for sub in INSTANCE_SUBDIRS:
                os.close(_ensure_dir(sub, gid, dir_fd=fd, mode=APPS_MODE))
        except BaseException:
            os.close(fd)
            raise
        return fd

    def trash_instance(
        self, space_id: UUID, gid: int, instance_id: UUID, stamp: str
    ) -> Path | None:
        """Move `apps/<instance_id>/` to `apps/.trash/<instance_id>-<stamp>/`; None if it's gone."""
        apps_fd = self._open_apps(space_id, gid)
        try:
            trash_fd = _ensure_dir(TRASH_DIR, gid, dir_fd=apps_fd, mode=APPS_MODE)
            name = f"{instance_id}-{stamp}"
            try:
                os.rename(str(instance_id), name, src_dir_fd=apps_fd, dst_dir_fd=trash_fd)
            except FileNotFoundError:
                return None
            finally:
                os.close(trash_fd)
        finally:
            os.close(apps_fd)
        return self.space_dir(space_id) / "apps" / TRASH_DIR / name

    def reconcile(self, spaces: Iterable[tuple[UUID, int]]) -> tuple[int, int]:
        """`ensure` every (space_id, gid); returns (ok, failed). Failures are logged, not raised."""
        ok = failed = 0
        for space_id, gid in spaces:
            try:
                self.ensure(space_id, gid)
                ok += 1
            except (OSError, StorageError) as exc:
                failed += 1
                logger.error("space %s: storage reconcile failed: %s", space_id, exc)
        return ok, failed

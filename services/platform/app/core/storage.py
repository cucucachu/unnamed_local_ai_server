"""Per-space directory trees under `SPACES_DIR` (docs/PLATFORM.md §5 "Host layout").

    <root>/<space_id>/          root:<gid> 2770
    <root>/<space_id>/files/    root:<gid> 2770
    <root>/<space_id>/apps/     root:<gid> 2770

`<root>` itself is root-owned and not group-writable, so `<space_id>` can be
trusted; everything below it is writable by the space's group (exec
containers, later), so each child is opened relative to its parent's fd with
`O_NOFOLLOW` and fixed up via `fchown`/`fchmod` - a swapped-in symlink makes
`ensure` fail instead of redirecting a root-privileged chown.

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
SUBDIRS = ("files", "apps")

_OPEN_DIR = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC


class StorageError(Exception):
    pass


def _fchown(fd: int, uid: int, gid: int) -> None:
    os.fchown(fd, uid, gid)


def _ensure_dir(name: str, gid: int, *, dir_fd: int | None = None) -> int:
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
        if stat.S_IMODE(os.fstat(fd).st_mode) != DIR_MODE:
            os.fchmod(fd, DIR_MODE)
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
                os.close(_ensure_dir(sub, gid, dir_fd=space_fd))
        finally:
            os.close(space_fd)

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

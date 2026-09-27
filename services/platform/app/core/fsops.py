"""Ownership-aware file operations inside a space's `files/` tree (docs/PLATFORM.md §5).

The platform runs as root, so everything it creates on a user's behalf is
handed over explicitly: files `uid:space_gid` mode 0660 (0770 when the
source was executable), directories `uid:space_gid` mode 2770. Callers pass
host paths already checked by `app.core.vfs` to lie inside the space.

Space trees are writable by the space's group (exec containers, later), so
nothing here follows a symlink it didn't resolve itself: files are opened
with `O_NOFOLLOW`, walks never descend through links, and copies recreate
links as links instead of copying what they point at.

Standard library only: `tests/test_fsops.py` runs this module as root in a
bare `python` container to check real ownership on disk.
"""

from __future__ import annotations

import os
import shutil
import stat
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

FILE_MODE = 0o660
EXEC_FILE_MODE = 0o770
DIR_MODE = 0o2770

_OPEN_DIR = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
_OPEN_NEW = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC
_OPEN_TRUNC = os.O_WRONLY | os.O_TRUNC | os.O_NOFOLLOW | os.O_CLOEXEC
# O_NONBLOCK: opening a FIFO someone planted must not hang the request.
_OPEN_READ = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC


@dataclass(frozen=True)
class Owner:
    uid: int
    gid: int


def _fchown(fd: int, uid: int, gid: int) -> None:
    os.fchown(fd, uid, gid)


def _lchown(path: Path, uid: int, gid: int) -> None:
    os.chown(path, uid, gid, follow_symlinks=False)


def _adopt(fd: int, owner: Owner, mode: int) -> None:
    _fchown(fd, owner.uid, owner.gid)
    # chown can clear setgid, so the mode goes second.
    os.fchmod(fd, mode)


def file_mode_for(source_mode: int) -> int:
    return EXEC_FILE_MODE if source_mode & stat.S_IXUSR else FILE_MODE


def open_regular(path: Path) -> BinaryIO:
    """Open an existing regular file for reading, never through a final symlink."""
    fd = os.open(path, _OPEN_READ)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise IsADirectoryError(f"{path.name}: not a regular file")
        os.set_blocking(fd, True)
        return os.fdopen(fd, "rb")
    except BaseException:
        os.close(fd)
        raise


@contextmanager
def open_for_write(
    path: Path, owner: Owner, *, mode: int = FILE_MODE, exclusive: bool = False
) -> Iterator[BinaryIO]:
    """Create `path` for `owner`, or truncate it in place (keeping its owner) unless `exclusive`."""
    try:
        fd = os.open(path, _OPEN_NEW, FILE_MODE)
        created = True
    except FileExistsError:
        if exclusive:
            raise
        fd = os.open(path, _OPEN_TRUNC)
        created = False
    try:
        if created:
            _adopt(fd, owner, mode)
        f = os.fdopen(fd, "wb")
    except BaseException:
        os.close(fd)
        raise
    with f:
        yield f


def make_dirs(root: Path, path: Path, owner: Owner) -> None:
    """`mkdir -p path`, where `path` lies inside the existing directory `root`.

    Raises `NotADirectoryError`/`FileExistsError` if something other than a
    directory is in the way.
    """
    missing: list[Path] = []
    p = path
    while p != root and not os.path.lexists(p):
        missing.append(p)
        p = p.parent
    for d in reversed(missing):
        try:
            os.mkdir(d, 0o700)
        except FileExistsError:
            continue
        fd = os.open(d, _OPEN_DIR)
        try:
            _adopt(fd, owner, DIR_MODE)
        finally:
            os.close(fd)
    if not path.is_dir() or path.is_symlink():
        raise FileExistsError(f"{path.name}: exists and is not a directory")


def _copy_file(src: Path, dst: Path, owner: Owner) -> None:
    with open_regular(src) as fin:
        st = os.fstat(fin.fileno())
        with open_for_write(dst, owner, mode=file_mode_for(st.st_mode), exclusive=True) as fout:
            shutil.copyfileobj(fin, fout, 1024 * 1024)
    os.utime(dst, ns=(st.st_atime_ns, st.st_mtime_ns), follow_symlinks=False)


def _copy_link(src: Path, dst: Path, owner: Owner) -> None:
    os.symlink(os.readlink(src), dst)
    _lchown(dst, owner.uid, owner.gid)


def copy(src: Path, dst: Path, owner: Owner) -> None:
    """Copy a file or a directory tree to the new path `dst`; every copy belongs to `owner`.

    Symlinks inside a copied tree are recreated as symlinks; FIFOs, sockets,
    and devices are skipped.
    """
    if not src.is_dir():
        _copy_file(src, dst, owner)
        return
    os.mkdir(dst, 0o700)
    fd = os.open(dst, _OPEN_DIR)
    try:
        _adopt(fd, owner, DIR_MODE)
    finally:
        os.close(fd)
    for entry in os.scandir(src):
        s, d = Path(entry.path), dst / entry.name
        if entry.is_symlink():
            _copy_link(s, d, owner)
        elif entry.is_dir(follow_symlinks=False):
            copy(s, d, owner)
        elif entry.is_file(follow_symlinks=False):
            _copy_file(s, d, owner)


def adopt_tree(path: Path, gid: int, uid: int = -1) -> None:
    """Give `path` and everything below it the group `gid` (and `uid`, unless -1).

    Directories become 2770 and regular files 0660/0770; symlinks are
    re-owned without being followed.
    """
    st = os.lstat(path)
    if stat.S_ISLNK(st.st_mode):
        _lchown(path, uid, gid)
        return
    if stat.S_ISDIR(st.st_mode):
        fd = os.open(path, _OPEN_DIR)
        try:
            _fchown(fd, uid, gid)
            os.fchmod(fd, DIR_MODE)
        finally:
            os.close(fd)
        for entry in os.scandir(path):
            adopt_tree(Path(entry.path), gid, uid)
        return
    if stat.S_ISREG(st.st_mode):
        fd = os.open(path, _OPEN_READ)
        try:
            _fchown(fd, uid, gid)
            os.fchmod(fd, file_mode_for(st.st_mode))
        finally:
            os.close(fd)
        return
    _lchown(path, uid, gid)


def remove(path: Path) -> None:
    """Delete a file or symlink, or a directory recursively (never following links)."""
    if path.is_symlink() or not path.is_dir():
        path.unlink()
    else:
        shutil.rmtree(path)

"""Ownership-aware file operations inside a space's `files/` tree (docs/PLATFORM.md §5).

The platform runs as root, so everything it creates on a user's behalf is
handed over explicitly: files `uid:space_gid` mode 0660 (0770 when the
source was executable), directories `uid:space_gid` mode 2770.

Space trees are writable by the space's members, so no path string is
ever handed to the kernel: callers pass `(root, parts)`, `app.core.beneath`
turns that into a directory fd and a name, and every operation is an `*at`
call on that fd with `O_NOFOLLOW` on the name. Walks (copy, delete,
regroup) descend by opening each child `O_NOFOLLOW` relative to its
parent's fd, decide what an entry is from the fd they opened, and recreate
links as links instead of following them.

Standard library only: `tests/test_fsops.py` runs this module as root in a
bare `python` container to check real ownership on disk.
"""

from __future__ import annotations

import ctypes
import errno
import os
import shutil
import stat
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import BinaryIO

from app.core import beneath
from app.core.beneath import Parts, Root

FILE_MODE = 0o660
EXEC_FILE_MODE = 0o770
DIR_MODE = 0o2770

_OPEN_DIR = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
# O_NONBLOCK: opening a FIFO someone planted must not hang the request.
_OPEN_READ = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC
_OPEN_NEW = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC


@dataclass(frozen=True)
class Owner:
    uid: int
    gid: int


def _fchown(fd: int, uid: int, gid: int) -> None:
    os.fchown(fd, uid, gid)


def _lchown(dir_fd: int, name: str, uid: int, gid: int) -> None:
    os.chown(name, uid, gid, dir_fd=dir_fd, follow_symlinks=False)


def _adopt(fd: int, owner: Owner, mode: int) -> None:
    _fchown(fd, owner.uid, owner.gid)
    # chown can clear setgid, so the mode goes second.
    os.fchmod(fd, mode)


def file_mode_for(source_mode: int) -> int:
    return EXEC_FILE_MODE if source_mode & stat.S_IXUSR else FILE_MODE


def _as_regular(fd: int, mode: str = "rb") -> BinaryIO:
    """`fd` as a blocking file object if it is a regular file; closed and refused otherwise."""
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise IsADirectoryError(errno.EISDIR, "not a regular file")
        os.set_blocking(fd, True)
        return os.fdopen(fd, mode)
    except BaseException:
        os.close(fd)
        raise


def open_regular(root: Root, parts: Parts, *, writable: bool = False) -> BinaryIO:
    """Open an existing regular file for reading (and writing, if `writable`)."""
    flags = (os.O_RDWR if writable else os.O_RDONLY) | os.O_NONBLOCK
    return _as_regular(beneath.open(root, parts, flags), "r+b" if writable else "rb")


def open_regular_at(dir_fd: int, name: str) -> BinaryIO:
    """Open the regular file `name` in `dir_fd` for reading, never through a symlink."""
    return _as_regular(os.open(name, _OPEN_READ, dir_fd=dir_fd))


@contextmanager
def open_for_write(
    root: Root, parts: Parts, owner: Owner, *, mode: int = FILE_MODE, exclusive: bool = False
) -> Iterator[BinaryIO]:
    """Create the file for `owner`, or truncate it in place (keeping its owner) unless `exclusive`."""
    try:
        fd = beneath.open(root, parts, _OPEN_NEW, FILE_MODE)
        created = True
    except FileExistsError:
        if exclusive:
            raise
        try:
            fd = beneath.open(root, parts, os.O_WRONLY | os.O_NONBLOCK)
        except OSError as exc:
            if exc.errno == errno.ENXIO:  # a FIFO nobody reads
                raise IsADirectoryError(errno.EISDIR, "not a regular file") from exc
            raise
        created = False
    if created:
        try:
            _adopt(fd, owner, mode)
            f = os.fdopen(fd, "wb")
        except BaseException:
            os.close(fd)
            raise
    else:
        f = _as_regular(fd, "wb")
        f.truncate(0)
    with f:
        yield f


def _mkdir_at(dir_fd: int, name: str, owner: Owner) -> int:
    """Create directory `name` for `owner` and return an open fd on it."""
    os.mkdir(name, 0o700, dir_fd=dir_fd)
    fd = os.open(name, _OPEN_DIR, dir_fd=dir_fd)
    try:
        _adopt(fd, owner, DIR_MODE)
    except BaseException:
        os.close(fd)
        raise
    return fd


def open_dir_at(dir_fd: int, name: str, owner: Owner) -> int:
    """An fd on directory `name` of `dir_fd`, made for `owner` if missing.

    Anything else by that name, a symlink included, is removed first, never followed.
    """
    try:
        return os.open(name, _OPEN_DIR, dir_fd=dir_fd)
    except FileNotFoundError:
        pass
    except OSError as exc:
        if exc.errno not in (errno.ELOOP, errno.ENOTDIR):
            raise
        remove_at(dir_fd, name)
    return _mkdir_at(dir_fd, name, owner)


def replace_file_at(dir_fd: int, name: str, data: bytes, owner: Owner) -> None:
    """Make `name` of `dir_fd` a new regular file of `owner` holding `data`.

    Written under a temporary name and renamed over what was there: a file or
    symlink is replaced (a link, not what it points at), a directory removed.
    """
    tmp = f".homeai-{os.urandom(8).hex()}"
    fd = os.open(tmp, _OPEN_NEW, FILE_MODE, dir_fd=dir_fd)
    try:
        try:
            _adopt(fd, owner, FILE_MODE)
            with os.fdopen(fd, "wb", closefd=False) as f:
                f.write(data)
        finally:
            os.close(fd)
        try:
            os.rename(tmp, name, src_dir_fd=dir_fd, dst_dir_fd=dir_fd)
        except IsADirectoryError:
            remove_at(dir_fd, name)
            os.rename(tmp, name, src_dir_fd=dir_fd, dst_dir_fd=dir_fd)
    except BaseException:
        try:
            os.unlink(tmp, dir_fd=dir_fd)
        except FileNotFoundError:
            pass
        raise


def make_dirs(root: Root, parts: Parts, owner: Owner) -> None:
    """`mkdir -p` below `root`.

    Raises `NotADirectoryError`/`FileExistsError` if something other than a
    directory is in the way.
    """
    for i in range(1, len(parts) + 1):
        sub = parts[:i]
        try:
            st = beneath.stat_at(root, sub)
        except FileNotFoundError:
            dir_fd, name = beneath.locate(root, sub)
            try:
                if name is not None:
                    os.close(_mkdir_at(dir_fd, name, owner))
            except FileExistsError:
                pass
            finally:
                os.close(dir_fd)
            st = beneath.stat_at(root, sub)
        if not stat.S_ISDIR(st.st_mode):
            if i < len(parts):
                raise NotADirectoryError(errno.ENOTDIR, "not a directory", sub[-1])
            raise FileExistsError(errno.EEXIST, "exists and is not a directory", sub[-1])


def _copy_file(src_fd: int, st: os.stat_result, dir_fd: int, name: str, owner: Owner) -> None:
    fd = os.open(name, _OPEN_NEW, FILE_MODE, dir_fd=dir_fd)
    try:
        _adopt(fd, owner, file_mode_for(st.st_mode))
        with (
            os.fdopen(src_fd, "rb", closefd=False) as fin,
            os.fdopen(fd, "wb", closefd=False) as fout,
        ):
            shutil.copyfileobj(fin, fout, 1024 * 1024)
        os.utime(fd, ns=(st.st_atime_ns, st.st_mtime_ns))
    finally:
        os.close(fd)


def _copy_link(src_dir: int, src_name: str, dir_fd: int, name: str, owner: Owner) -> None:
    os.symlink(os.readlink(src_name, dir_fd=src_dir), name, dir_fd=dir_fd)
    _lchown(dir_fd, name, owner.uid, owner.gid)


def _copy_children(src: int, dst: int, owner: Owner, skip: tuple[int, int]) -> None:
    with os.scandir(src) as it:
        entries = list(it)
    for entry in entries:
        try:
            st = entry.stat(follow_symlinks=False)
        except FileNotFoundError:
            continue
        if (st.st_dev, st.st_ino) == skip:
            continue
        if stat.S_ISLNK(st.st_mode):
            _copy_link(src, entry.name, dst, entry.name, owner)
        elif stat.S_ISDIR(st.st_mode) or stat.S_ISREG(st.st_mode):
            try:
                fd = os.open(entry.name, _OPEN_READ, dir_fd=src)
            except FileNotFoundError:
                continue
            try:
                _copy_open(fd, dst, entry.name, owner, skip)
            finally:
                os.close(fd)


def _copy_open(
    src_fd: int, dir_fd: int, name: str, owner: Owner, skip: tuple[int, int] | None = None
) -> None:
    """Copy what `src_fd` is open on to the new `name` in `dir_fd`.

    `skip` is the top of the copy, never copied into itself.
    """
    st = os.fstat(src_fd)
    if stat.S_ISREG(st.st_mode):
        _copy_file(src_fd, st, dir_fd, name, owner)
        return
    if not stat.S_ISDIR(st.st_mode):
        raise IsADirectoryError(errno.EISDIR, "not a regular file", name)
    fd = _mkdir_at(dir_fd, name, owner)
    try:
        if skip is None:
            top = os.fstat(fd)
            skip = (top.st_dev, top.st_ino)
        _copy_children(src_fd, fd, owner, skip)
    finally:
        os.close(fd)


def copy(src_root: Root, src: Parts, dst_root: Root, dst: Parts, owner: Owner) -> None:
    """Copy a file or a directory tree to the new path `dst`; every copy belongs to `owner`.

    `src` itself may be reached through a symlink inside its space; below it,
    symlinks are recreated as symlinks, and FIFOs, sockets, and devices are
    skipped.
    """
    src_fd = beneath.open(src_root, src, os.O_RDONLY | os.O_NONBLOCK)
    try:
        with beneath.parent(dst_root, dst) as (dir_fd, name):
            _copy_open(src_fd, dir_fd, name, owner)
    finally:
        os.close(src_fd)


def copy_at(src_dir: int, src_name: str, dir_fd: int, name: str, owner: Owner) -> None:
    """`copy` for the entry `src_name` of `src_dir` itself: a symlink is copied as a link."""
    if stat.S_ISLNK(os.stat(src_name, dir_fd=src_dir, follow_symlinks=False).st_mode):
        _copy_link(src_dir, src_name, dir_fd, name, owner)
        return
    fd = os.open(src_name, _OPEN_READ, dir_fd=src_dir)
    try:
        _copy_open(fd, dir_fd, name, owner)
    finally:
        os.close(fd)


def _load_renameat2():
    try:
        fn = ctypes.CDLL(None, use_errno=True).renameat2
    except (OSError, AttributeError):
        return None
    fn.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    fn.restype = ctypes.c_int
    return fn


_renameat2 = _load_renameat2()
_RENAME_NOREPLACE = 1


def rename_noreplace(src_dir: int, src_name: str, dst_dir: int, dst_name: str) -> None:
    """`renameat` that fails with `FileExistsError` instead of replacing an existing `dst_name`.

    There is no fallback to a plain rename: without `renameat2` (glibc >= 2.28,
    Linux >= 3.15, a filesystem that supports `RENAME_NOREPLACE`) this raises.
    """
    if _renameat2 is None:
        raise OSError(errno.ENOSYS, "renameat2 is not in this libc (needs glibc >= 2.28)")
    src, dst = os.fsencode(src_name), os.fsencode(dst_name)
    if _renameat2(src_dir, src, dst_dir, dst, _RENAME_NOREPLACE):
        code = ctypes.get_errno()
        raise OSError(code, os.strerror(code), src_name, None, dst_name)


def move(src_root: Root, src: Parts, dst_root: Root, dst: Parts, *, gid: int | None = None) -> None:
    """Rename `src` (not followed) to the new path `dst`, never replacing what is there.

    With `gid`, regroup the moved tree.
    """
    with (
        beneath.parent(src_root, src) as (src_dir, src_name),
        beneath.parent(dst_root, dst) as (dir_fd, name),
    ):
        rename_noreplace(src_dir, src_name, dir_fd, name)
        if gid is not None:
            adopt_at(dir_fd, name, gid)


def adopt_at(dir_fd: int, name: str, gid: int, uid: int = -1) -> None:
    """Give entry `name` of `dir_fd` and everything below it the group `gid` (and `uid`, unless -1).

    Directories become 2770 and regular files 0660/0770; anything else,
    symlinks included, is re-owned without being followed.
    """
    st = os.stat(name, dir_fd=dir_fd, follow_symlinks=False)
    if stat.S_ISDIR(st.st_mode) or stat.S_ISREG(st.st_mode):
        try:
            fd = os.open(name, _OPEN_READ, dir_fd=dir_fd)
        except OSError as exc:
            if exc.errno != errno.ELOOP:
                raise
        else:
            try:
                st = os.fstat(fd)
                _fchown(fd, uid, gid)
                if stat.S_ISDIR(st.st_mode):
                    os.fchmod(fd, DIR_MODE)
                    with os.scandir(fd) as it:
                        children = [entry.name for entry in it]
                    for child in children:
                        try:
                            adopt_at(fd, child, gid, uid)
                        except FileNotFoundError:
                            continue
                elif stat.S_ISREG(st.st_mode):
                    os.fchmod(fd, file_mode_for(st.st_mode))
            finally:
                os.close(fd)
            return
    _lchown(dir_fd, name, uid, gid)


def remove_at(dir_fd: int, name: str) -> None:
    """Delete entry `name` of `dir_fd`: a file or symlink, or a directory recursively."""
    try:
        os.unlink(name, dir_fd=dir_fd)
        return
    except IsADirectoryError:
        pass
    fd = os.open(name, _OPEN_DIR, dir_fd=dir_fd)
    try:
        with os.scandir(fd) as it:
            children = [entry.name for entry in it]
        for child in children:
            try:
                remove_at(fd, child)
            except FileNotFoundError:
                continue
    finally:
        os.close(fd)
    os.rmdir(name, dir_fd=dir_fd)


def remove(root: Root, parts: Parts) -> None:
    """Delete a file or symlink (never what it points at), or a directory recursively."""
    with beneath.parent(root, parts) as (dir_fd, name):
        remove_at(dir_fd, name)

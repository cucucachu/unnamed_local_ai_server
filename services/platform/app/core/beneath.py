"""Paths below a directory fd that can't be raced out of it (docs/PLATFORM.md §5 "Race-free access").

Space trees are writable by their members (exec containers mount them), so
any component of a path can become a symlink between a check and a use.
Nothing here hands the kernel more than one path component: `locate`
starts at the root fd, opens each component `O_PATH | O_NOFOLLOW` relative
to its parent's fd, and expands symlinks itself. A relative target is
walked starting at the link's directory, `..` popping the walk's own stack
of directory fds, never past the root; an absolute target counts only if it is
the root's own path (`Root.path`) or below it, and is then walked from the
root fd. Anything else is `EXDEV`; more than `MAX_SYMLINKS` expansions is
`ELOOP`. Callers get a directory fd and a single name and act with an `*at`
call on that fd, `O_NOFOLLOW` on the name, so a swap after the walk fails
instead of being followed.

Why a walk instead of `openat2(RESOLVE_BENEATH)`: `RESOLVE_BENEATH` refuses
every absolute symlink, and the files API follows absolute links that stay
inside the space; it also needs Linux >= 5.6 plus a seccomp profile that
allows it, i.e. a second code path for when it's missing. The walk gives the
same containment with `openat`/`fstat`/`readlinkat` alone.

Standard library only, like `fsops`: `tests/test_fsops.py` runs both as root
in a bare `python` container.
"""

from __future__ import annotations

import errno
import os
import stat
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass

MAX_SYMLINKS = 40
# Walks repeated when the final name becomes a symlink between walk and open.
_REWALKS = 3

_OPEN_PATH = os.O_PATH | os.O_NOFOLLOW | os.O_CLOEXEC
_OPEN_PATH_DIR = os.O_PATH | os.O_DIRECTORY | os.O_CLOEXEC

Parts = Sequence[str]


@dataclass(frozen=True)
class Root:
    """An open directory; `path` is where it was opened (None: follow no absolute link)."""

    fd: int
    path: str | None = None

    def below(self, target: str) -> list[str] | None:
        """The components of absolute link target `target` below `path`, or None if outside."""
        if self.path is None:
            return None
        if target == self.path:
            return []
        prefix = self.path.rstrip("/") + "/"
        return target[len(prefix) :].split("/") if target.startswith(prefix) else None


def _error(code: int, name: str) -> OSError:
    return OSError(code, os.strerror(code), name)


def locate(
    root: Root, parts: Parts, *, follow: bool = True, symlinks: bool = True
) -> tuple[int, str | None]:
    """`(dir_fd, name)`: the directory holding the path's last component, and that component.

    `dir_fd` is an `O_PATH` fd the caller closes. `name` is None when the path
    is that directory itself (the root, or a final link to a directory). With
    `follow` a final symlink is expanded, so `name` is missing or wasn't a
    symlink when looked at; without it `name` may be a symlink. Earlier
    components must exist and be directories. With `symlinks=False` meeting
    any symlink on the way (a followed last component included) is `ELOOP`.
    """
    stack = [os.open(".", _OPEN_PATH_DIR, dir_fd=root.fd)]
    todo = list(reversed(parts))
    links = 0
    try:
        while todo:
            name = todo.pop()
            if name in ("", "."):
                continue
            if name == "..":
                if len(stack) == 1:
                    raise _error(errno.EXDEV, name)
                os.close(stack.pop())
                continue
            if "/" in name or "\x00" in name:
                raise ValueError(f"not a path component: {name!r}")
            last = not todo
            if last and not follow:
                return stack.pop(), name
            try:
                fd = os.open(name, _OPEN_PATH, dir_fd=stack[-1])
            except FileNotFoundError:
                if last:
                    return stack.pop(), name
                raise
            try:
                mode = os.fstat(fd).st_mode
            except BaseException:
                os.close(fd)
                raise
            if last or not stat.S_ISDIR(mode):
                os.close(fd)
            if stat.S_ISLNK(mode):
                links += 1
                if not symlinks or links > MAX_SYMLINKS:
                    raise _error(errno.ELOOP, name)
                try:
                    target = os.readlink(name, dir_fd=stack[-1])
                except OSError as exc:
                    if exc.errno != errno.EINVAL:
                        raise
                    todo.append(name)  # no longer a symlink: look again
                    continue
                if target.startswith("/"):
                    below = root.below(target)
                    if below is None:
                        raise _error(errno.EXDEV, name)
                    while len(stack) > 1:
                        os.close(stack.pop())
                    todo.extend(reversed(below))
                else:
                    todo.extend(reversed(target.split("/")))
                continue
            if last:
                return stack.pop(), name
            if not stat.S_ISDIR(mode):
                raise _error(errno.ENOTDIR, name)
            stack.append(fd)
        return stack.pop(), None
    finally:
        for fd in stack:
            os.close(fd)


def open(
    root: Root,
    parts: Parts,
    flags: int,
    mode: int = 0o600,
    *,
    follow: bool = True,
    symlinks: bool = True,
) -> int:
    """`os.open` of `parts` below `root`, the final name opened with `O_NOFOLLOW`.

    An `O_PATH` open that lands on a symlink is refused like any other (`ELOOP`).
    """
    for attempt in range(_REWALKS):
        dir_fd, name = locate(root, parts, follow=follow, symlinks=symlinks)
        try:
            if name is None:
                fd = os.open(".", flags | os.O_CLOEXEC, mode, dir_fd=dir_fd)
            else:
                fd = os.open(name, flags | os.O_NOFOLLOW | os.O_CLOEXEC, mode, dir_fd=dir_fd)
                if flags & os.O_PATH and stat.S_ISLNK(os.fstat(fd).st_mode):
                    os.close(fd)
                    raise _error(errno.ELOOP, name)
        except OSError as exc:
            if exc.errno == errno.ELOOP and follow and symlinks and attempt < _REWALKS - 1:
                continue
            raise
        finally:
            os.close(dir_fd)
        return fd
    raise AssertionError("unreachable")  # pragma: no cover


@contextmanager
def parent(root: Root, parts: Parts, *, symlinks: bool = True) -> Iterator[tuple[int, str]]:
    """The directory holding the (non-empty) path's last component, and its name, not followed."""
    if not parts:
        raise ValueError("the root has no parent")
    dir_fd, name = locate(root, parts, follow=False, symlinks=symlinks)
    try:
        yield dir_fd, name
    finally:
        os.close(dir_fd)


def stat_at(root: Root, parts: Parts) -> os.stat_result:
    """`stat` of the path (symlinks inside `root` followed)."""
    fd = open(root, parts, os.O_PATH)
    try:
        return os.fstat(fd)
    finally:
        os.close(fd)


def lstat_at(root: Root, parts: Parts) -> os.stat_result:
    """`lstat` of the path: a final symlink is described, not followed."""
    if not parts:
        return os.fstat(root.fd)
    with parent(root, parts) as (dir_fd, name):
        return os.stat(name, dir_fd=dir_fd, follow_symlinks=False)


def is_inside(root: Root, parts: Parts, ancestor: os.stat_result) -> bool:
    """Whether the directory at `parts` is `ancestor` or below it, going up by fd to `root`."""
    top = os.fstat(root.fd)
    fd = open(root, parts, _OPEN_PATH_DIR)
    try:
        while True:
            st = os.fstat(fd)
            if os.path.samestat(st, ancestor):
                return True
            if os.path.samestat(st, top):
                return False
            up = os.open("..", _OPEN_PATH_DIR, dir_fd=fd)
            os.close(fd)
            fd = up
            if os.path.samestat(os.fstat(fd), st):
                return False
    finally:
        os.close(fd)

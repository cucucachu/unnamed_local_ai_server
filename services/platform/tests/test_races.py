"""Symlink swaps racing the files API and `app.core.fsops` never reach another space.

Space trees are writable by their members, so any component of a path can
become a symlink to another space between the guard's check and the
operation, or in the middle of one. Three angles:

- every files route, with the swap made after `resolve_virtual_path` has
  checked the path and before the operation opens the space;
- a swap in the middle of `beneath.locate`'s walk;
- a thread atomically exchanging a directory and a symlink to another
  space (`renameat2(RENAME_EXCHANGE)`) while read, write, mkdir, move,
  copy, delete and chown run against paths through it.

The other space is snapshotted before and compared after; no chown may be
recorded on anything in it.
"""

from __future__ import annotations

import ctypes
import errno
import os
import stat
import threading
import time
from pathlib import Path

import pytest

from app.core import agentfs, beneath, fsops, vfs
from tests.files_world import FILES, World

SECRET = b"family secret"


def _snapshot(root: Path) -> dict[str, tuple]:
    """Every entry below `root` (never following links): type, mode, owner, content or target."""
    out = {}
    for dirpath, dirnames, filenames in os.walk(root):
        for name in [*dirnames, *filenames]:
            p = Path(dirpath) / name
            st = p.lstat()
            if stat.S_ISLNK(st.st_mode):
                body = os.readlink(p)
            elif stat.S_ISREG(st.st_mode):
                body = p.read_bytes()
            else:
                body = None
            out[str(p.relative_to(root))] = (st.st_mode, st.st_uid, st.st_gid, body)
    return out


def _chowned_inside(chowns: dict[Path, tuple[int, int]], root: Path) -> list[Path]:
    return [p for p in chowns if p == root or root in p.parents]


# --- every route: swapped between the guard's check and the operation ------------------


def _route_requests():
    video = "/personal/dir/v.mp4"
    upload = [("file", ("up.txt", b"x", "text/plain"))]
    invalid = (422, "invalid_path")
    return [
        ("GET", "", {"params": {"path": "/personal/dir"}}, invalid),
        ("GET", "/stat", {"params": {"path": "/personal/dir/f.txt"}}, invalid),
        ("GET", "/download", {"params": {"path": "/personal/dir/f.txt"}}, invalid),
        ("GET", "/stream", {"params": {"path": "/personal/dir/f.txt"}}, invalid),
        ("HEAD", "/stream", {"params": {"path": "/personal/dir/f.txt"}}, (422, None)),
        ("GET", "/thumbnail", {"params": {"path": video}}, invalid),
        ("POST", "/upload", {"data": {"path": "/personal/dir"}, "files": upload}, invalid),
        ("POST", "/mkdir", {"json": {"path": "/personal/dir/new/deeper"}}, invalid),
        ("POST", "/move", {"json": {"src": "/personal/dir/f.txt", "dst": "/personal/m.txt"}},
         invalid),
        ("POST", "/move", {"json": {"src": "/personal/g.txt", "dst": "/personal/dir/g.txt"}},
         invalid),
        ("POST", "/move",
         {"json": {"src": "/personal/dir/f.txt", "dst": "/spaces/family/moved.txt"}}, invalid),
        ("POST", "/copy", {"json": {"src": "/personal/dir", "dst": "/personal/copy"}}, invalid),
        ("POST", "/copy", {"json": {"src": "/personal/g.txt", "dst": "/personal/dir/g.txt"}},
         invalid),
        ("POST", "/rename", {"json": {"path": "/personal/dir/f.txt", "name": "h.txt"}}, invalid),
        ("DELETE", "", {"params": {"path": "/personal/dir/f.txt"}}, invalid),
        ("DELETE", "", {"params": {"path": "/personal/dir/sub"}}, invalid),
        ("POST", "/read", {"json": {"path": "/personal/dir/f.txt"}}, invalid),
        ("POST", "/write", {"json": {"path": "/personal/dir/w.txt", "content": "x"}}, invalid),
        ("PUT", "/content", {"params": {"path": "/personal/dir/c.txt"}, "content": b"x"},
         invalid),
        ("POST", "/edit",
         {"json": {"path": "/personal/dir/f.txt", "old_string": "family", "new_string": "x"}},
         invalid),
        # The link itself is what a delete of `dir` acts on.
        ("DELETE", "", {"params": {"path": "/personal/dir"}}, (204, None)),
    ]  # fmt: skip


async def test_swap_between_check_and_use_on_every_route(world: World, chowns, monkeypatch):
    home, family = world.home("alice"), world.family_root
    (home / "g.txt").write_text("g")
    (family / "f.txt").write_bytes(SECRET)
    (family / "v.mp4").write_bytes(SECRET)
    (family / "sub").mkdir()
    (family / "sub" / "g.txt").write_bytes(SECRET)
    before = _snapshot(family)

    def reset() -> None:
        if (home / "dir").is_symlink():
            (home / "dir").unlink()
        if (home / "dir.orig").exists():
            (home / "dir.orig").rename(home / "dir")
        (home / "dir" / "sub").mkdir(parents=True, exist_ok=True)
        (home / "dir" / "f.txt").write_text("mine")
        (home / "dir" / "v.mp4").write_text("mine")

    swaps = []

    def swap() -> None:
        if not (home / "dir").is_symlink():
            (home / "dir").rename(home / "dir.orig")
            (home / "dir").symlink_to(family)
            swaps.append(1)

    real_open_root = vfs.Resolved.open_root

    def open_root_after_swap(self):
        swap()
        return real_open_root(self)

    monkeypatch.setattr(vfs.Resolved, "open_root", open_root_after_swap)

    for method, route, kw, (code, detail) in _route_requests():
        reset()
        swaps.clear()
        chowns.clear()
        response = await world.client.request(
            method, f"{FILES}{route}", headers=world.headers["alice"], **kw
        )
        where = (method, route, kw)
        assert swaps, where
        assert response.status_code == code, (where, response.text)
        if detail:
            assert response.json()["detail"] == detail, where
        assert SECRET not in response.content, where
        assert _snapshot(family) == before, where
        assert not _chowned_inside(chowns, family), where


async def test_grep_and_glob_through_a_swapped_dir_find_nothing(world: World, monkeypatch):
    home, family = world.home("alice"), world.family_root
    (home / "dir").mkdir()
    (home / "dir" / "mine.txt").write_text("family secret, but mine")
    (family / "f.txt").write_bytes(SECRET)
    real_open_root = agentfs.Tree.open_root

    def open_root_after_swap(self):
        if not (home / "dir").is_symlink():
            (home / "dir").rename(home / "dir.orig")
            (home / "dir").symlink_to(family)
        return real_open_root(self)

    monkeypatch.setattr(agentfs.Tree, "open_root", open_root_after_swap)
    for route, body in (
        ("/grep", {"pattern": "family secret", "path": "/personal/dir"}),
        ("/glob", {"pattern": "**/*.txt", "path": "/personal/dir"}),
    ):
        if (home / "dir").is_symlink():
            (home / "dir").unlink()
            (home / "dir.orig").rename(home / "dir")
        response = await world.client.post(
            f"{FILES}{route}", json=body, headers=world.headers["alice"]
        )
        assert response.status_code == 200, response.text
        assert response.json()["matches"] == [], route


# --- a swap in the middle of the walk ----------------------------------------------------


@pytest.fixture
def two_spaces(tmp_path: Path):
    a, b = tmp_path.resolve() / "a", tmp_path.resolve() / "b"
    (a / "d").mkdir(parents=True)
    (a / "d" / "f.txt").write_bytes(b"mine")
    (b / "sub").mkdir(parents=True)
    (b / "f.txt").write_bytes(SECRET)
    (b / "sub" / "g.txt").write_bytes(SECRET)
    fd = os.open(a, os.O_RDONLY | os.O_DIRECTORY)
    try:
        yield beneath.Root(fd, str(a)), a, b
    finally:
        os.close(fd)


def test_swap_mid_walk_stays_in_the_opened_directory(two_spaces, monkeypatch):
    root, a, b = two_spaces
    real_open = os.open
    swapped = []

    def open_then_swap(path, flags, *args, **kwargs):
        fd = real_open(path, flags, *args, **kwargs)
        if path == "d" and flags & os.O_PATH and not swapped:
            os.rename(a / "d", a / "d.orig")
            os.symlink(b, a / "d")
            swapped.append(1)
        return fd

    monkeypatch.setattr(os, "open", open_then_swap)
    owner = fsops.Owner(os.getuid(), os.getgid())
    with fsops.open_for_write(root, ("d", "x.txt"), owner) as f:
        f.write(b"x")
    monkeypatch.setattr(os, "open", real_open)
    assert swapped
    assert (a / "d.orig" / "x.txt").read_bytes() == b"x"
    assert not (b / "x.txt").exists()


# --- a thread exchanging a directory and a symlink to another space -------------------

_RENAME_EXCHANGE = 2
_AT_FDCWD = -100


def _exchange(x: Path, y: Path) -> None:
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.renameat2(_AT_FDCWD, bytes(x), _AT_FDCWD, bytes(y), _RENAME_EXCHANGE) != 0:
        code = ctypes.get_errno()
        raise OSError(code, os.strerror(code))


def test_hammered_swaps_never_escape(two_spaces, chowns):
    """`d` is always there, flipping between a directory of space a and a link to space b."""
    root, a, b = two_spaces
    (a / "d.alt").symlink_to(b)
    real_dir = os.open(a / "d", os.O_RDONLY | os.O_DIRECTORY)
    before = _snapshot(b)
    owner = fsops.Owner(os.getuid(), os.getgid())
    stop = threading.Event()
    flips, hammer_errors = [0], []

    def hammer() -> None:
        try:
            while not stop.is_set():
                _exchange(a / "d", a / "d.alt")
                flips[0] += 1
        except OSError as exc:
            hammer_errors.append(exc)

    def seed() -> None:
        for name, body in (("f.txt", b"mine"), ("del.txt", b"x")):
            fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644, dir_fd=real_dir)
            os.write(fd, body)
            os.close(fd)
        os.makedirs(f"/proc/self/fd/{real_dir}/sub", exist_ok=True)

    def read() -> None:
        with fsops.open_regular(root, ("d", "f.txt")) as f:
            assert f.read() != SECRET

    def copy() -> None:
        try:
            fsops.copy(root, ("d",), root, ("copy",), owner)
            copied = _snapshot(a / "copy")
            assert all(entry[3] != SECRET for entry in copied.values()), copied
        finally:
            if (a / "copy").exists():
                fsops.remove(root, ("copy",))

    def write() -> None:
        with fsops.open_for_write(root, ("d", "w.txt"), owner) as f:
            f.write(b"w")

    def move() -> None:
        fsops.move(root, ("d", "f.txt"), root, ("moved.txt",))
        os.rename(a / "moved.txt", f"/proc/self/fd/{real_dir}/f.txt")

    ops = {
        "read": read,
        "write": write,
        "mkdir": lambda: fsops.make_dirs(root, ("d", "m", "n"), owner),
        "move": move,
        "copy": copy,
        "delete": lambda: fsops.remove(root, ("d", "del.txt")),
        "delete_tree": lambda: fsops.remove(root, ("d", "sub")),
        "chown": lambda: fsops.adopt_at(root.fd, "d", 12345),
        "move_regroup": lambda: fsops.move(root, ("d", "sub"), root, ("regrouped",), gid=12345),
    }
    outcomes = {name: {"ok": 0, "refused": 0} for name in ops}
    thread = threading.Thread(target=hammer, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            for name, op in ops.items():
                seed()
                try:
                    op()
                    outcomes[name]["ok"] += 1
                except OSError as exc:
                    assert exc.errno in (errno.EXDEV, errno.ENOENT, errno.ENOTDIR,
                                         errno.ELOOP, errno.EEXIST), (name, exc)  # fmt: skip
                    outcomes[name]["refused"] += 1
                if (a / "regrouped").exists():
                    fsops.remove(root, ("regrouped",))
    finally:
        stop.set()
        thread.join()
        os.close(real_dir)
    assert _snapshot(b) == before
    assert not _chowned_inside(chowns, b)
    # Both sides of the swap were hit: every operation worked on the real directory
    # and some were refused through the link.
    assert not hammer_errors, hammer_errors
    assert all(seen["ok"] for seen in outcomes.values()), (flips, outcomes)
    assert sum(seen["refused"] for seen in outcomes.values()) >= 10, (flips, outcomes)

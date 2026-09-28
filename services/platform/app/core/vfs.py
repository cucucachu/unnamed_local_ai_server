"""Virtual file paths and the one guard every files route goes through (docs/PLATFORM.md §5).

    /                    synthetic, read-only: lists `personal` and `spaces`
    /spaces              synthetic, read-only: the caller's shared spaces by slug
    /personal/...        the caller's own personal space
    /spaces/<slug>/...   a shared space (members only)

`resolve_virtual_path(conn, principal, storage, vpath, need)` parses the
path, finds the space, authorizes the caller through
`spaces.authorize_space` (a non-member gets the same `404 not_found` as a
slug that doesn't exist; a personal space's slug is not addressable under
`/spaces`), and keeps the rest as components below the space's `files/`
dir. Rules carried over from agent-server's `resolve_files_path`: a null
byte is refused, and symlinks are only followed while they stay in that
`files/` dir - a link to another space, even one the caller belongs to, is
refused like any other escape. `..` segments are refused outright rather
than resolved, so a path can't hop between spaces lexically either.

There is no host path: operations open the space's `files/` dir
(`open_files_root`) and act below that fd through `app.core.beneath`, so
a component swapped for a symlink after this check can't redirect them.
The check here only gives an escape its `422` before anything is touched.
"""

from __future__ import annotations

import errno
import os
import stat
from collections.abc import Iterator
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass
from typing import Literal
from uuid import UUID

from psycopg import AsyncConnection

from app.core import beneath, spaces
from app.core.beneath import Root
from app.core.errors import Forbidden, InvalidInput, NotFound
from app.core.principal import Principal
from app.core.storage import SpaceStorage

PERSONAL = "personal"
SPACES = "spaces"
# Top-level folder of every space holding app sources (`/<space>/Apps/<slug>/`).
APPS = "Apps"

Kind = Literal["root", "spaces", "space"]


def parse(vpath: str) -> tuple[str, ...]:
    """The path's segments: a leading `/` is optional, empty and `.` segments are dropped."""
    if "\x00" in vpath:
        raise InvalidInput("invalid_path")
    parts = tuple(p for p in vpath.split("/") if p not in ("", "."))
    if ".." in parts:
        raise InvalidInput("invalid_path")
    return parts


def join(*parts: str) -> str:
    return "/" + "/".join(p.strip("/") for p in parts if p.strip("/"))


def space_prefix(space: spaces.Row) -> str:
    return join(PERSONAL) if space["kind"] == "personal" else join(SPACES, space["slug"])


_OPEN_DIR = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC


@contextmanager
def open_files_root(storage: SpaceStorage, space_id: UUID) -> Iterator[Root]:
    """The space's `files/` dir, open; `404` unless it is a plain directory (its parent is
    group-writable)."""
    top = storage.root.resolve()
    try:
        top_fd = os.open(top, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
        try:
            space_fd = os.open(str(space_id), _OPEN_DIR, dir_fd=top_fd)
        finally:
            os.close(top_fd)
        try:
            fd = os.open("files", _OPEN_DIR, dir_fd=space_fd)
        finally:
            os.close(space_fd)
    except OSError as exc:
        raise NotFound("not_found") from exc
    try:
        yield Root(fd, str(top / str(space_id) / "files"))
    finally:
        os.close(fd)


@dataclass(frozen=True)
class Resolved:
    vpath: str
    kind: Kind
    space: spaces.Row | None = None
    role: str | None = None
    storage: SpaceStorage | None = None
    # Components below the space's `files/` dir; () is the space root.
    rel: tuple[str, ...] = ()

    @property
    def writable(self) -> bool:
        return self.kind == "space" and spaces.ROLE_RANK[self.role] >= spaces.ROLE_RANK["editor"]

    @property
    def prefix(self) -> str:
        return space_prefix(self.space)

    @property
    def is_space_root(self) -> bool:
        return self.kind == "space" and not self.rel

    def open_root(self) -> AbstractContextManager[Root]:
        return open_files_root(self.storage, self.space["id"])

    def is_apps_folder(self, root: Root) -> bool:
        """The space's reserved top-level `Apps` directory (never deleted, renamed or moved).

        A file or symlink squatting on the name isn't protected, so it can be cleared away.
        """
        if self.kind != "space" or self.rel != (APPS,):
            return False
        try:
            return stat.S_ISDIR(beneath.lstat_at(root, self.rel).st_mode)
        except FileNotFoundError:
            return False

    def child(self, name: str) -> str:
        return f"{self.vpath.rstrip('/')}/{name}"


def _refuse_escape(root: Root, parts: tuple[str, ...]) -> None:
    try:
        os.close(beneath.locate(root, parts)[0])
    except (FileNotFoundError, NotADirectoryError):
        pass  # a missing path is the operation's to report
    except OSError as exc:
        if exc.errno in (errno.EXDEV, errno.ELOOP):
            raise InvalidInput("invalid_path") from exc
        raise


async def resolve_virtual_path(
    conn: AsyncConnection,
    principal: Principal,
    storage: SpaceStorage,
    vpath: str,
    need: spaces.Need,
    *,
    follow: bool = True,
) -> Resolved:
    """`follow=False` checks only the parent, for operations on a final symlink itself
    (delete, move, rename) rather than what it points at."""
    parts = parse(vpath)
    if not parts or parts == (SPACES,):
        if need != "read":
            raise Forbidden("read_only")
        return Resolved(join(*parts), "root" if not parts else "spaces")

    head, rest = parts[0], parts[1:]
    if head == PERSONAL:
        space = await spaces.get_personal_space(conn, principal.user_id)
    elif head == SPACES:
        space = await spaces.get_space_by_slug(conn, parts[1])
        if space["kind"] != "shared":
            raise NotFound("not_found")
        rest = parts[2:]
    else:
        raise NotFound("not_found")

    access = await spaces.authorize_space(conn, principal, space["id"], need)
    with open_files_root(storage, space["id"]) as root:
        _refuse_escape(root, rest if follow else rest[:-1])
    return Resolved(
        join(space_prefix(space), *rest),
        "space",
        space=access.space,
        role=access.role,
        storage=storage,
        rel=tuple(rest),
    )

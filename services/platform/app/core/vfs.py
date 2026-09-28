"""Virtual file paths and the one guard every files route goes through (docs/PLATFORM.md §5).

    /                    synthetic, read-only: lists `personal` and `spaces`
    /spaces              synthetic, read-only: the caller's shared spaces by slug
    /personal/...        the caller's own personal space
    /spaces/<slug>/...   a shared space (members only)

`resolve_virtual_path(conn, principal, storage, vpath, need)` parses the
path, finds the space, authorizes the caller through
`spaces.authorize_space` (a non-member gets the same `404 not_found` as a
slug that doesn't exist; a personal space's slug is not addressable under
`/spaces`), and maps the rest onto the space's `files/` dir. Rules carried
over from agent-server's `resolve_files_path`: a null byte is refused, and
after resolving symlinks the host path must still be that `files/` dir or
below it - a link to another space, even one the caller belongs to, is
refused like any other escape. `..` segments are refused outright rather
than resolved, so a path can't hop between spaces lexically either.
"""

from __future__ import annotations

import stat
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Literal

from psycopg import AsyncConnection

from app.core import spaces
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


def contain(root: Path, rel: str) -> Path:
    """`root / rel` with symlinks resolved; refused unless it is `root` or below it."""
    if "\x00" in rel:
        raise InvalidInput("invalid_path")
    try:
        p = (root / rel).resolve()
    except (OSError, RuntimeError) as exc:
        raise InvalidInput("invalid_path") from exc
    if p != root and root not in p.parents:
        raise InvalidInput("invalid_path")
    return p


@dataclass(frozen=True)
class Resolved:
    vpath: str
    kind: Kind
    space: spaces.Row | None = None
    role: str | None = None
    files_root: Path | None = None
    host_path: Path | None = None

    @property
    def writable(self) -> bool:
        return self.kind == "space" and spaces.ROLE_RANK[self.role] >= spaces.ROLE_RANK["editor"]

    @property
    def prefix(self) -> str:
        return space_prefix(self.space)

    @property
    def is_space_root(self) -> bool:
        return self.kind == "space" and self.host_path == self.files_root

    @property
    def is_apps_folder(self) -> bool:
        """The space's reserved top-level `Apps` directory (never deleted, renamed or moved).

        A file or symlink squatting on the name isn't protected, so it can be cleared away.
        """
        if self.kind != "space" or self.host_path != self.files_root / APPS:
            return False
        try:
            return stat.S_ISDIR(self.host_path.lstat().st_mode)
        except FileNotFoundError:
            return False

    def vpath_of(self, host: Path) -> str:
        """Virtual path of a host path inside this space's `files/` dir."""
        if host == self.files_root:
            return self.prefix
        return f"{self.prefix}/{host.relative_to(self.files_root).as_posix()}"

    def child(self, name: str) -> str:
        return f"{self.vpath.rstrip('/')}/{name}"


def files_root(storage: SpaceStorage, space_id) -> Path:
    """The space's `files/` dir; refused if it was swapped for a symlink (its parent is group-writable)."""
    root = storage.space_dir(space_id).resolve() / "files"
    if root.is_symlink() or not root.is_dir():
        raise NotFound("not_found")
    return root


async def resolve_virtual_path(
    conn: AsyncConnection,
    principal: Principal,
    storage: SpaceStorage,
    vpath: str,
    need: spaces.Need,
    *,
    follow: bool = True,
) -> Resolved:
    """`follow=False` resolves only the parent, so the host path names a final symlink itself
    (for delete, move, rename) rather than what it points at."""
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
    root = files_root(storage, space["id"])
    if not rest:
        host = root
    elif follow:
        host = contain(root, str(PurePosixPath(*rest)))
    else:
        host = contain(root, str(PurePosixPath(*rest[:-1])) if rest[:-1] else "") / rest[-1]
    return Resolved(
        join(space_prefix(space), *rest),
        "space",
        space=access.space,
        role=access.role,
        files_root=root,
        host_path=host,
    )

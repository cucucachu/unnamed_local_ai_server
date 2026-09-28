"""What an agent run's exec container may mount, and as whom (docs/PLATFORM.md §6).

code-exec-manager presents the run's delegation; the answer is computed from
the database on every call, so a revoked session, a disabled user, or a
changed membership shows up in the very next `ensure`/`execute`:

- `uid` is the user's; `gid` is their personal space's, the container's
  primary group; `gids` is every space they belong to (personal first).
- one bind per space: the personal space at `/files/personal`, each shared
  space at `/files/spaces/<slug>`, read-only for a viewer.
- one read-only bind per live app instance in those spaces (M13-02): its
  published `apps/<instance_id>/ro/` (holding `data.sqlite`, §7 "Data") at
  `/app-data/personal/<app-slug>` or `/app-data/spaces/<space-slug>/<app-slug>`,
  for every role (the copy is read-only anyway). A second instance with the
  same app slug in one space gets `-<first 8 of its id>` appended.

Host paths are under `spaces_host_dir` - `${SPACES_DIR}` as the Docker
daemon sees it, which is not this container's `/data/spaces` mount. A space
whose `files/` dir, or an instance whose `ro/` dir, isn't a plain directory
here (found by an `O_NOFOLLOW` walk from the spaces root, `app.core.beneath`)
is left out rather than handed to the daemon, which would follow a symlink.
"""

from __future__ import annotations

import logging
import os
import stat
from dataclasses import dataclass
from pathlib import PurePosixPath
from uuid import UUID

from psycopg import AsyncConnection

from app.core import appdb, beneath, sessions, spaces, vfs
from app.core.errors import NotFound, ServerError, Unauthorized
from app.core.storage import SpaceStorage
from app.core.tokens import TokenError, TokenService

logger = logging.getLogger(__name__)

_OPEN_PATH_DIR = os.O_PATH | os.O_DIRECTORY

MOUNT_ROOT = PurePosixPath("/files")
APP_DATA_ROOT = PurePosixPath("/app-data")

_LIVE_INSTANCES = """
SELECT i.id, i.space_id, a.slug
FROM app_instances i JOIN apps a ON a.id = i.app_id
WHERE i.space_id = ANY(%s) AND i.uninstalled_at IS NULL
ORDER BY i.created_at, i.id
"""


@dataclass(frozen=True)
class Mount:
    host_path: str
    container_path: str
    read_only: bool


@dataclass(frozen=True)
class ExecGrants:
    uid: int
    gid: int
    gids: list[int]
    mounts: list[Mount]


def _container_path(space: spaces.Row, root: PurePosixPath = MOUNT_ROOT) -> str:
    if space["kind"] == "personal":
        return str(root / vfs.PERSONAL)
    return str(root / vfs.SPACES / space["slug"])


def _has_plain_ro(storage: SpaceStorage, space_id: UUID, instance_id: UUID) -> bool:
    """`<space_id>/apps/<instance_id>/ro` is a directory reached without following a link."""
    parts = (str(space_id), "apps", str(instance_id), appdb.RO_DIR)
    try:
        top = os.open(storage.root.resolve(), os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
        try:
            fd = beneath.open(beneath.Root(top), parts, _OPEN_PATH_DIR, symlinks=False)
        finally:
            os.close(top)
    except OSError:
        return False
    try:
        return stat.S_ISDIR(os.fstat(fd).st_mode)
    finally:
        os.close(fd)


async def _app_data_mounts(
    conn: AsyncConnection, storage: SpaceStorage, host_root: PurePosixPath, member_of: list
) -> list[Mount]:
    by_id = {s["id"]: s for s in member_of}
    cur = await conn.execute(_LIVE_INSTANCES, (list(by_id),))
    mounts, taken = [], set()
    for row in await cur.fetchall():
        space = by_id[row["space_id"]]
        target = str(PurePosixPath(_container_path(space, APP_DATA_ROOT)) / row["slug"])
        if target in taken:
            target = f"{target}-{str(row['id'])[:8]}"
        if not _has_plain_ro(storage, space["id"], row["id"]):
            logger.warning(
                "exec grants: instance %s has no plain %s/ dir; not mounted",
                row["id"],
                appdb.RO_DIR,
            )
            continue
        taken.add(target)
        mounts.append(
            Mount(
                host_path=str(
                    host_root / str(space["id"]) / "apps" / str(row["id"]) / appdb.RO_DIR
                ),
                container_path=target,
                read_only=True,
            )
        )
    return mounts


async def for_delegation(
    conn: AsyncConnection,
    tokens: TokenService,
    storage: SpaceStorage,
    spaces_host_dir: str,
    delegation: str,
) -> ExecGrants:
    if not spaces_host_dir:
        raise ServerError("exec_unconfigured")
    try:
        claims = tokens.verify_token(delegation, act="agent")
        user_id, session_id = UUID(str(claims["sub"])), UUID(str(claims.get("sid")))
    except (TokenError, ValueError) as exc:
        raise Unauthorized("unauthenticated") from exc
    if not isinstance(claims.get("thr"), str):
        raise Unauthorized("unauthenticated")
    user = await sessions.load_active(conn, session_id, user_id)
    if user is None:
        raise Unauthorized("unauthenticated")

    member_of = await spaces.list_user_spaces(conn, user_id)
    personal = next((s for s in member_of if s["kind"] == "personal"), None)
    if personal is None:
        raise NotFound("no_personal_space")

    host_root = PurePosixPath(spaces_host_dir)
    mounts = []
    for space in member_of:
        try:
            with vfs.open_files_root(storage, space["id"]):
                pass
        except NotFound:
            logger.warning(
                "exec grants: space %s has no plain files/ dir; not mounted", space["id"]
            )
            continue
        mounts.append(
            Mount(
                host_path=str(host_root / str(space["id"]) / "files"),
                container_path=_container_path(space),
                read_only=space["role"] == "viewer",
            )
        )
    mounts += await _app_data_mounts(conn, storage, host_root, member_of)
    return ExecGrants(
        uid=user["uid"],
        gid=personal["gid"],
        gids=[s["gid"] for s in member_of],
        mounts=mounts,
    )

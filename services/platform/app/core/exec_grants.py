"""What an agent run's exec container may mount, and as whom (docs/PLATFORM.md §6).

code-exec-manager presents the run's delegation; the answer is computed from
the database on every call, so a revoked session, a disabled user, or a
changed membership shows up in the very next `ensure`/`execute`:

- `uid` is the user's; `gid` is their personal space's, the container's
  primary group; `gids` is every space they belong to (personal first).
- one bind per space: the personal space at `/files/personal`, each shared
  space at `/files/spaces/<slug>`, read-only for a viewer.

Host paths are under `spaces_host_dir` - `${SPACES_DIR}` as the Docker
daemon sees it, which is not this container's `/data/spaces` mount. A space
whose `files/` dir isn't a plain directory here is left out rather than
handed to the daemon, which would follow a symlink.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import PurePosixPath
from uuid import UUID

from psycopg import AsyncConnection

from app.core import sessions, spaces, vfs
from app.core.errors import NotFound, ServerError, Unauthorized
from app.core.storage import SpaceStorage
from app.core.tokens import TokenError, TokenService

logger = logging.getLogger(__name__)

MOUNT_ROOT = PurePosixPath("/files")


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


def _container_path(space: spaces.Row) -> str:
    if space["kind"] == "personal":
        return str(MOUNT_ROOT / vfs.PERSONAL)
    return str(MOUNT_ROOT / vfs.SPACES / space["slug"])


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
            vfs.files_root(storage, space["id"])
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
    return ExecGrants(
        uid=user["uid"],
        gid=personal["gid"],
        gids=[s["gid"] for s in member_of],
        mounts=mounts,
    )

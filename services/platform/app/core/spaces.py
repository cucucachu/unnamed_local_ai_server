"""Spaces, memberships, and the one authorization check for space data (docs/PLATFORM.md §5).

`authorize_space(conn, principal, space_id, need)` is how every route that
touches a space's contents (files, app instances, ...) decides access:

- not a member, unknown id, or archived -> `404 not_found` (ids don't leak);
- `need="manage"` from an agent delegation -> `403 agent_not_allowed`;
- a member whose role is below `need` -> `403 insufficient_role`
  (`viewer` = read, `editor` = write, `owner` = manage).

Admins get nothing extra there. The only admin override is
`authorize_membership`, used by the membership routes alone: a stepped-up
admin may list and change any active space's members, never read its data.

Every space's directory tree is created in the same transaction as its row
(before commit, so a storage failure rolls the row back).
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any, Literal
from uuid import UUID

from psycopg import AsyncConnection
from psycopg.errors import UniqueViolation

from app.core.errors import Conflict, Forbidden, InvalidInput, NotFound
from app.core.principal import Principal
from app.core.storage import SpaceStorage

ROLES = ("owner", "editor", "viewer")
ROLE_RANK = {"viewer": 1, "editor": 2, "owner": 3}
NEED_RANK = {"read": 1, "write": 2, "manage": 3}
Need = Literal["read", "write", "manage"]

SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,39}$")
MAX_SLUG_LENGTH = 40
# Top-level names of the virtual file tree (`/personal`, `/spaces/<slug>`).
RESERVED_SLUGS = frozenset({"personal", "spaces"})
MAX_NAME_LENGTH = 64

SPACE_COLUMNS = "s.id, s.slug, s.name, s.kind, s.gid, s.owner_user_id, s.created_at, s.archived_at"
MEMBER_COLUMNS = "u.id AS user_id, u.username, u.display_name, m.role, m.created_at AS added_at"

Row = dict[str, Any]


@dataclass(frozen=True)
class SpaceAccess:
    space: Row
    # The caller's own role; None when granted by the admin override.
    role: str | None


# --- input rules ---------------------------------------------------------------


def normalize_slug(slug: str) -> str:
    normalized = slug.strip().lower()
    if not SLUG_RE.fullmatch(normalized):
        raise InvalidInput("invalid_slug")
    if normalized in RESERVED_SLUGS:
        raise InvalidInput("reserved_slug")
    return normalized


def normalize_name(name: str) -> str:
    normalized = name.strip()
    if not 1 <= len(normalized) <= MAX_NAME_LENGTH or not normalized.isprintable():
        raise InvalidInput("invalid_name")
    return normalized


def personal_slug_candidates(username: str) -> Iterator[str]:
    """`alice`, then `alice-2`, `alice-3`, ...: skips reserved names; `.`/`_` become `-`."""
    base = re.sub(r"[^a-z0-9-]", "-", username)[:MAX_SLUG_LENGTH]
    if base not in RESERVED_SLUGS:
        yield base
    for n in range(2, 1000):
        suffix = f"-{n}"
        yield base[: MAX_SLUG_LENGTH - len(suffix)] + suffix


# --- creation ------------------------------------------------------------------


async def _insert_space(
    conn: AsyncConnection, *, slug: str, name: str, kind: str, owner_user_id: UUID | None
) -> Row | None:
    """Insert a space row; None if the slug is taken."""
    try:
        async with conn.transaction():
            cur = await conn.execute(
                "INSERT INTO spaces AS s (slug, name, kind, owner_user_id) "
                f"VALUES (%s, %s, %s, %s) RETURNING {SPACE_COLUMNS}",
                (slug, name, kind, owner_user_id),
            )
            return await cur.fetchone()
    except UniqueViolation as exc:
        if exc.diag.constraint_name == "spaces_slug_key":
            return None
        raise


async def _add_owner(conn: AsyncConnection, space_id: UUID, user_id: UUID) -> None:
    await conn.execute(
        "INSERT INTO space_members (space_id, user_id, role) VALUES (%s, %s, 'owner')",
        (space_id, user_id),
    )


async def create_personal_space(conn: AsyncConnection, user: Row, storage: SpaceStorage) -> Row:
    """The user's personal space; call inside the transaction that creates the user."""
    async with conn.transaction():
        for slug in personal_slug_candidates(user["username"]):
            space = await _insert_space(
                conn, slug=slug, name=user["display_name"], kind="personal",
                owner_user_id=user["id"],
            )  # fmt: skip
            if space is not None:
                break
        else:
            raise Conflict("slug_taken")
        await _add_owner(conn, space["id"], user["id"])
        storage.ensure(space["id"], space["gid"])
    return space


async def create_shared_space(
    conn: AsyncConnection, *, slug: str, name: str, owner_id: UUID, storage: SpaceStorage
) -> Row:
    slug, name = normalize_slug(slug), normalize_name(name)
    async with conn.transaction():
        space = await _insert_space(conn, slug=slug, name=name, kind="shared", owner_user_id=None)
        if space is None:
            raise Conflict("slug_taken")
        await _add_owner(conn, space["id"], owner_id)
        storage.ensure(space["id"], space["gid"])
    return space


async def backfill_personal_spaces(conn: AsyncConnection, storage: SpaceStorage) -> int:
    """Give every user without a personal space one (users created before 0003)."""
    cur = await conn.execute(
        "SELECT id, username, display_name FROM users u WHERE NOT EXISTS ("
        "SELECT 1 FROM spaces s WHERE s.kind = 'personal' AND s.owner_user_id = u.id) "
        "ORDER BY created_at, username"
    )
    created = 0
    for user in await cur.fetchall():
        try:
            await create_personal_space(conn, user, storage)
            created += 1
        except UniqueViolation as exc:
            if exc.diag.constraint_name != "spaces_personal_owner_key":
                raise
    return created


async def all_space_dirs(conn: AsyncConnection) -> list[tuple[UUID, int]]:
    """(id, gid) of every space, archived ones included - their data stays on disk."""
    cur = await conn.execute("SELECT id, gid FROM spaces ORDER BY created_at, id")
    return [(row["id"], row["gid"]) for row in await cur.fetchall()]


# --- lookup ----------------------------------------------------------------------


async def get_space(conn: AsyncConnection, space_id: UUID) -> Row:
    """An active (not archived) space."""
    cur = await conn.execute(
        f"SELECT {SPACE_COLUMNS} FROM spaces s WHERE s.id = %s AND s.archived_at IS NULL",
        (space_id,),
    )
    row = await cur.fetchone()
    if row is None:
        raise NotFound("not_found")
    return row


async def get_space_by_slug(conn: AsyncConnection, slug: str) -> Row:
    cur = await conn.execute(
        f"SELECT {SPACE_COLUMNS} FROM spaces s WHERE s.slug = %s AND s.archived_at IS NULL",
        (slug.strip().lower(),),
    )
    row = await cur.fetchone()
    if row is None:
        raise NotFound("not_found")
    return row


async def get_personal_space(conn: AsyncConnection, user_id: UUID) -> Row:
    cur = await conn.execute(
        f"SELECT {SPACE_COLUMNS} FROM spaces s "
        "WHERE s.kind = 'personal' AND s.owner_user_id = %s AND s.archived_at IS NULL",
        (user_id,),
    )
    row = await cur.fetchone()
    if row is None:
        raise NotFound("not_found")
    return row


async def list_user_spaces(conn: AsyncConnection, user_id: UUID) -> list[Row]:
    """Active spaces the user belongs to, with their `role`: personal first, then by name."""
    cur = await conn.execute(
        f"SELECT {SPACE_COLUMNS}, m.role FROM spaces s "
        "JOIN space_members m ON m.space_id = s.id AND m.user_id = %s "
        "WHERE s.archived_at IS NULL "
        "ORDER BY s.kind = 'shared', lower(s.name), s.slug",
        (user_id,),
    )
    return await cur.fetchall()


async def list_all_spaces(conn: AsyncConnection, viewer_id: UUID) -> list[Row]:
    """Every space, archived included, with `viewer_id`'s role (or None)."""
    cur = await conn.execute(
        f"SELECT {SPACE_COLUMNS}, m.role FROM spaces s "
        "LEFT JOIN space_members m ON m.space_id = s.id AND m.user_id = %s "
        "ORDER BY s.created_at, s.slug",
        (viewer_id,),
    )
    return await cur.fetchall()


async def list_all_spaces_with_members(conn: AsyncConnection) -> list[Row]:
    """Every space with a `members` list of {username, role} (recovery CLI)."""
    cur = await conn.execute(
        f"SELECT {SPACE_COLUMNS}, COALESCE(json_agg(json_build_object("
        "'username', u.username, 'role', m.role) ORDER BY u.username) "
        "FILTER (WHERE u.id IS NOT NULL), '[]') AS members "
        "FROM spaces s LEFT JOIN space_members m ON m.space_id = s.id "
        "LEFT JOIN users u ON u.id = m.user_id "
        "GROUP BY s.id ORDER BY s.created_at, s.slug"
    )
    return await cur.fetchall()


# --- authorization -------------------------------------------------------------


async def authorize_space(
    conn: AsyncConnection, principal: Principal, space_id: UUID, need: Need
) -> SpaceAccess:
    """The caller's access to an active space, or raise (see the module docstring)."""
    required = NEED_RANK[need]
    cur = await conn.execute(
        f"SELECT {SPACE_COLUMNS}, m.role FROM spaces s "
        "JOIN space_members m ON m.space_id = s.id AND m.user_id = %s "
        "WHERE s.id = %s AND s.archived_at IS NULL",
        (principal.user_id, space_id),
    )
    row = await cur.fetchone()
    if row is None:
        raise NotFound("not_found")
    if need == "manage" and principal.is_agent:
        raise Forbidden("agent_not_allowed")
    role = row.pop("role")
    if ROLE_RANK[role] < required:
        raise Forbidden("insufficient_role")
    return SpaceAccess(row, role)


def _is_stepped_up_admin(principal: Principal) -> bool:
    return not principal.is_agent and principal.role == "admin" and principal.stepped_up


async def authorize_membership(
    conn: AsyncConnection, principal: Principal, space_id: UUID, need: Need
) -> SpaceAccess:
    """`authorize_space`, plus the admin override - for the membership routes only."""
    try:
        return await authorize_space(conn, principal, space_id, need)
    except (NotFound, Forbidden):
        if not _is_stepped_up_admin(principal):
            raise
    return SpaceAccess(await get_space(conn, space_id), None)


# --- changes -----------------------------------------------------------------------


def _reject_personal(space: Row) -> None:
    if space["kind"] == "personal":
        raise Conflict("personal_space")


async def rename_space(conn: AsyncConnection, space_id: UUID, name: str) -> Row:
    name = normalize_name(name)
    cur = await conn.execute(
        f"UPDATE spaces s SET name = %s WHERE s.id = %s AND s.archived_at IS NULL "
        f"RETURNING {SPACE_COLUMNS}",
        (name, space_id),
    )
    row = await cur.fetchone()
    if row is None:
        raise NotFound("not_found")
    return row


async def archive_space(conn: AsyncConnection, space: Row) -> None:
    """Hide a shared space from everyone; its rows, members, and files are kept."""
    _reject_personal(space)
    await conn.execute(
        "UPDATE spaces SET archived_at = now() WHERE id = %s AND archived_at IS NULL",
        (space["id"],),
    )


async def list_members(conn: AsyncConnection, space_id: UUID) -> list[Row]:
    cur = await conn.execute(
        f"SELECT {MEMBER_COLUMNS} FROM space_members m JOIN users u ON u.id = m.user_id "
        "WHERE m.space_id = %s "
        "ORDER BY CASE m.role WHEN 'owner' THEN 0 WHEN 'editor' THEN 1 ELSE 2 END, u.username",
        (space_id,),
    )
    return await cur.fetchall()


async def _get_member(conn: AsyncConnection, space_id: UUID, user_id: UUID) -> Row:
    cur = await conn.execute(
        f"SELECT {MEMBER_COLUMNS} FROM space_members m JOIN users u ON u.id = m.user_id "
        "WHERE m.space_id = %s AND m.user_id = %s",
        (space_id, user_id),
    )
    row = await cur.fetchone()
    if row is None:
        raise NotFound("not_found")
    return row


def _check_role(role: str) -> None:
    if role not in ROLES:
        raise InvalidInput("invalid_role")


async def _lock_space(conn: AsyncConnection, space_id: UUID) -> None:
    """Serialize membership changes on one space (last-owner checks)."""
    cur = await conn.execute(
        "SELECT 1 FROM spaces WHERE id = %s AND archived_at IS NULL FOR UPDATE", (space_id,)
    )
    if await cur.fetchone() is None:
        raise NotFound("not_found")


async def _other_owners(conn: AsyncConnection, space_id: UUID, user_id: UUID) -> int:
    cur = await conn.execute(
        "SELECT count(*) AS n FROM space_members "
        "WHERE space_id = %s AND role = 'owner' AND user_id <> %s",
        (space_id, user_id),
    )
    return (await cur.fetchone())["n"]


async def add_member(conn: AsyncConnection, space: Row, user_id: UUID, role: str) -> Row:
    _reject_personal(space)
    _check_role(role)
    async with conn.transaction():
        await _lock_space(conn, space["id"])
        cur = await conn.execute("SELECT disabled_at FROM users WHERE id = %s", (user_id,))
        user = await cur.fetchone()
        if user is None:
            raise InvalidInput("unknown_user")
        if user["disabled_at"] is not None:
            raise Conflict("user_disabled")
        cur = await conn.execute(
            "INSERT INTO space_members (space_id, user_id, role) VALUES (%s, %s, %s) "
            "ON CONFLICT DO NOTHING",
            (space["id"], user_id, role),
        )
        if cur.rowcount == 0:
            raise Conflict("already_member")
    return await _get_member(conn, space["id"], user_id)


async def set_member_role(conn: AsyncConnection, space: Row, user_id: UUID, role: str) -> Row:
    _reject_personal(space)
    _check_role(role)
    async with conn.transaction():
        await _lock_space(conn, space["id"])
        current = await _get_member(conn, space["id"], user_id)
        demoting = current["role"] == "owner" and role != "owner"
        if demoting and await _other_owners(conn, space["id"], user_id) == 0:
            raise Conflict("last_owner")
        await conn.execute(
            "UPDATE space_members SET role = %s WHERE space_id = %s AND user_id = %s",
            (role, space["id"], user_id),
        )
    return await _get_member(conn, space["id"], user_id)


async def remove_member(conn: AsyncConnection, space: Row, user_id: UUID) -> None:
    _reject_personal(space)
    async with conn.transaction():
        await _lock_space(conn, space["id"])
        current = await _get_member(conn, space["id"], user_id)
        if current["role"] == "owner" and await _other_owners(conn, space["id"], user_id) == 0:
            raise Conflict("last_owner")
        await conn.execute(
            "DELETE FROM space_members WHERE space_id = %s AND user_id = %s",
            (space["id"], user_id),
        )

"""The app registry: apps, their versions, and instances installed in spaces (docs/PLATFORM.md §7).

- An app's source is exactly `/<space>/Apps/<slug>/` (`/personal/Apps/<slug>`
  or `/spaces/<s>/Apps/<slug>`); registering needs `write` on that space, a
  package that passes `manifest.validate_package`, and `app.json`'s `slug`
  equal to the folder name. The space's `Apps` folder is created if missing.
- Visible to a user: apps whose source space they can read, plus apps
  installed in a space they belong to. For the latter alone, the source
  (`source_path`, the working version) stays hidden.
- Installing needs `write` on the target space and a visible app. `tracks`
  is `working` (only in the app's own source space, D15) or a published
  version's id (M14). Each install gets `apps/<instance_id>/` in the target
  space; uninstalling moves it to `apps/.trash/<instance_id>-<stamp>/`.

Agent delegations have their user's rights here (D6): none of this is an
admin or auth action.
"""

from __future__ import annotations

import errno
import os
from datetime import UTC
from pathlib import Path
from typing import Any
from uuid import UUID

import anyio.to_thread
from psycopg import AsyncConnection
from psycopg.errors import UniqueViolation
from psycopg.types.json import Jsonb

from app.core import beneath, fsops, manifest, spaces, vfs
from app.core.errors import Conflict, InvalidApp, InvalidInput, NotFound
from app.core.principal import Principal
from app.core.storage import SpaceStorage

Row = dict[str, Any]

WORKING = "working"

_VERSION_JSON = (
    "json_build_object('id', {v}.id, 'version', {v}.version, 'kind', {v}.kind, "
    "'commit', {v}.commit, 'manifest', {v}.manifest, 'bundle_path', {v}.bundle_path, "
    "'created_at', {v}.created_at, 'published_at', {v}.published_at)"
)

# %(user)s: the viewer. Rows carry `source_readable`; `_present` hides the
# source when it's false.
_VISIBLE_APPS = f"""
SELECT a.id, a.slug, a.name, a.source_space_id, a.source_path, a.created_by,
       a.created_at, a.archived_at, {_VERSION_JSON.format(v="v")} AS working_version,
       (sm.user_id IS NOT NULL) AS source_readable
FROM apps a
LEFT JOIN spaces ss ON ss.id = a.source_space_id AND ss.archived_at IS NULL
LEFT JOIN space_members sm ON sm.space_id = ss.id AND sm.user_id = %(user)s
LEFT JOIN app_versions v ON v.app_id = a.id AND v.kind = 'working'
WHERE a.archived_at IS NULL
  AND (sm.user_id IS NOT NULL OR EXISTS (
      SELECT 1 FROM app_instances i
      JOIN spaces isp ON isp.id = i.space_id AND isp.archived_at IS NULL
      JOIN space_members im ON im.space_id = i.space_id AND im.user_id = %(user)s
      WHERE i.app_id = a.id AND i.uninstalled_at IS NULL))
"""

_INSTANCES = """
SELECT i.id, i.app_id, i.space_id,
       CASE WHEN i.tracks = 'working' THEN 'working' ELSE i.version_id::text END AS tracks,
       i.installed_by, i.granted_permissions, i.created_at,
       json_build_object('id', a.id, 'slug', a.slug, 'name', a.name, 'version', v.version,
                         'icon', v.manifest -> 'homeai' ->> 'icon') AS app
FROM app_instances i
JOIN apps a ON a.id = i.app_id
LEFT JOIN app_versions v ON v.id = i.version_id
    OR (i.version_id IS NULL AND v.app_id = i.app_id AND v.kind = 'working')
WHERE i.uninstalled_at IS NULL
"""


def _present(row: Row) -> Row:
    readable = row.pop("source_readable")
    if not readable:
        row["source_path"] = None
        row["working_version"] = None
    return row


# --- source paths ----------------------------------------------------------------


def parse_source_path(vpath: str) -> str:
    """The app slug of `/personal/Apps/<slug>` or `/spaces/<s>/Apps/<slug>`."""
    parts = vfs.parse(vpath)
    if len(parts) == 3 and parts[0] == vfs.PERSONAL and parts[1] == vfs.APPS:
        slug = parts[2]
    elif len(parts) == 4 and parts[0] == vfs.SPACES and parts[2] == vfs.APPS:
        slug = parts[3]
    else:
        raise InvalidInput("invalid_source_path")
    if not spaces.SLUG_RE.fullmatch(slug):
        raise InvalidInput("invalid_source_path")
    return slug


async def _resolve_source(
    conn: AsyncConnection, principal: Principal, storage: SpaceStorage, vpath: str
) -> tuple[vfs.Resolved, str]:
    slug = parse_source_path(vpath)
    r = await vfs.resolve_virtual_path(conn, principal, storage, vpath, "write")
    # `Apps` and the app folder must be the real thing, not a link to one.
    with r.open_root() as root:
        try:
            os.close(beneath.locate(root, r.rel, symlinks=False)[0])
        except (FileNotFoundError, NotADirectoryError):
            pass
        except OSError as exc:
            if exc.errno != errno.ELOOP:
                raise
            raise InvalidInput("invalid_source_path") from exc
    return r, slug


def _ensure_apps_folder(r: vfs.Resolved, owner: fsops.Owner) -> None:
    try:
        with r.open_root() as root:
            fsops.make_dirs(root, (vfs.APPS,), owner)
    except (FileExistsError, NotADirectoryError) as exc:
        raise Conflict("apps_folder_not_a_directory") from exc


def _validate_source(r: vfs.Resolved, slug: str) -> tuple[Any, list[manifest.Diagnostic]]:
    with r.open_root() as root:
        try:
            fd = beneath.open(root, r.rel, os.O_RDONLY | os.O_DIRECTORY, symlinks=False)
        except (FileNotFoundError, NotADirectoryError):
            return manifest.validate_package(None, slug)
        except OSError as exc:
            if exc.errno != errno.ELOOP:
                raise
            raise InvalidInput("invalid_source_path") from exc
    try:
        return manifest.validate_package(fd, slug)
    finally:
        os.close(fd)


def _diagnostics(found: list[manifest.Diagnostic]) -> list[dict[str, str]]:
    return [d.as_dict() for d in found]


# --- apps ------------------------------------------------------------------------


async def get_visible_app(conn: AsyncConnection, principal: Principal, app_id: UUID) -> Row:
    cur = await conn.execute(
        _VISIBLE_APPS + " AND a.id = %(id)s", {"user": principal.user_id, "id": app_id}
    )
    row = await cur.fetchone()
    if row is None:
        raise NotFound("not_found")
    return _present(row)


async def list_visible_apps(conn: AsyncConnection, principal: Principal) -> list[Row]:
    cur = await conn.execute(
        _VISIBLE_APPS + " ORDER BY lower(a.name), a.slug, a.id", {"user": principal.user_id}
    )
    return [_present(row) for row in await cur.fetchall()]


async def register_app(
    conn: AsyncConnection, principal: Principal, storage: SpaceStorage, source_path: str
) -> Row:
    """Register the package at `source_path`, or raise `InvalidApp` with its diagnostics."""
    r, slug = await _resolve_source(conn, principal, storage, source_path)
    owner = fsops.Owner(principal.uid, r.space["gid"])
    await anyio.to_thread.run_sync(_ensure_apps_folder, r, owner)
    doc, found = await anyio.to_thread.run_sync(_validate_source, r, slug)
    if found:
        raise InvalidApp(_diagnostics(found))
    try:
        async with conn.transaction():
            cur = await conn.execute(
                "INSERT INTO apps (slug, name, source_space_id, source_path, created_by) "
                "VALUES (%s, %s, %s, %s, %s) RETURNING id",
                (slug, doc["name"], r.space["id"], r.vpath, principal.user_id),
            )
            app_id = (await cur.fetchone())["id"]
            await conn.execute(
                "INSERT INTO app_versions (app_id, version, kind, manifest) "
                "VALUES (%s, %s, 'working', %s)",
                (app_id, doc["version"], Jsonb(doc)),
            )
    except UniqueViolation as exc:
        if exc.diag.constraint_name == "apps_source_space_id_slug_key":
            raise Conflict("app_exists") from exc
        raise
    return await get_visible_app(conn, principal, app_id)


async def validate_app(
    conn: AsyncConnection, principal: Principal, storage: SpaceStorage, app_id: UUID
) -> tuple[Row, list[dict[str, str]]]:
    """Re-validate the source; when it passes, the working version takes its manifest."""
    app = await get_visible_app(conn, principal, app_id)
    await spaces.authorize_space(conn, principal, app["source_space_id"], "write")
    r, slug = await _resolve_source(conn, principal, storage, app["source_path"])
    doc, found = await anyio.to_thread.run_sync(_validate_source, r, slug)
    if not found:
        async with conn.transaction():
            await conn.execute("UPDATE apps SET name = %s WHERE id = %s", (doc["name"], app_id))
            await conn.execute(
                "UPDATE app_versions SET version = %s, manifest = %s "
                "WHERE app_id = %s AND kind = 'working'",
                (doc["version"], Jsonb(doc), app_id),
            )
        app = await get_visible_app(conn, principal, app_id)
    return app, _diagnostics(found)


async def list_all_apps(conn: AsyncConnection) -> list[Row]:
    """Every app with its source space's slug and live instance count (recovery CLI)."""
    cur = await conn.execute(
        "SELECT a.id, a.slug, a.name, a.source_path, s.slug AS space, a.created_at, "
        "a.archived_at, v.version, (SELECT count(*) FROM app_instances i "
        "WHERE i.app_id = a.id AND i.uninstalled_at IS NULL) AS instances "
        "FROM apps a JOIN spaces s ON s.id = a.source_space_id "
        "LEFT JOIN app_versions v ON v.app_id = a.id AND v.kind = 'working' "
        "ORDER BY a.created_at, a.id"
    )
    return await cur.fetchall()


# --- instances ---------------------------------------------------------------------


async def list_instances(conn: AsyncConnection, principal: Principal, space_id: UUID) -> list[Row]:
    await spaces.authorize_space(conn, principal, space_id, "read")
    cur = await conn.execute(
        _INSTANCES + " AND i.space_id = %s ORDER BY i.created_at, i.id", (space_id,)
    )
    return await cur.fetchall()


async def _get_instance(conn: AsyncConnection, instance_id: UUID) -> Row:
    cur = await conn.execute(_INSTANCES + " AND i.id = %s", (instance_id,))
    return await cur.fetchone()


async def _pinned_version(conn: AsyncConnection, app_id: UUID, tracks: str) -> Row:
    try:
        version_id = UUID(tracks)
    except ValueError as exc:
        raise InvalidInput("invalid_tracks") from exc
    cur = await conn.execute(
        "SELECT id, manifest FROM app_versions WHERE id = %s AND app_id = %s AND kind = 'published'",
        (version_id, app_id),
    )
    row = await cur.fetchone()
    if row is None:
        raise InvalidInput("unknown_version")
    return row


async def install_app(
    conn: AsyncConnection,
    principal: Principal,
    storage: SpaceStorage,
    space_id: UUID,
    app_id: UUID,
    tracks: str,
) -> Row:
    access = await spaces.authorize_space(conn, principal, space_id, "write")
    app = await get_visible_app(conn, principal, app_id)
    if tracks == WORKING:
        if app["source_space_id"] != space_id:
            raise InvalidInput("working_requires_source_space")
        version_id, doc = None, app["working_version"]["manifest"]
    else:
        pinned = await _pinned_version(conn, app_id, tracks)
        version_id, doc = pinned["id"], pinned["manifest"]
    granted = doc.get("homeai", {}).get("permissions", {})
    try:
        async with conn.transaction():
            cur = await conn.execute(
                "INSERT INTO app_instances "
                "(app_id, space_id, tracks, version_id, installed_by, granted_permissions) "
                "VALUES (%s, %s, %s, %s, %s, %s) RETURNING id",
                (
                    app_id, space_id, WORKING if version_id is None else "version",
                    version_id, principal.user_id, Jsonb(granted),
                ),
            )  # fmt: skip
            instance_id = (await cur.fetchone())["id"]
            storage.ensure_instance(space_id, access.space["gid"], instance_id)
    except UniqueViolation as exc:
        if exc.diag.constraint_name == "app_instances_app_space_key":
            raise Conflict("already_installed") from exc
        raise
    return await _get_instance(conn, instance_id)


async def uninstall_app(
    conn: AsyncConnection,
    principal: Principal,
    storage: SpaceStorage,
    space_id: UUID,
    instance_id: UUID,
) -> Path | None:
    """Mark the instance uninstalled and move its data dir to the trash; returns where it went."""
    access = await spaces.authorize_space(conn, principal, space_id, "write")
    async with conn.transaction():
        cur = await conn.execute(
            "UPDATE app_instances SET uninstalled_at = now() "
            "WHERE id = %s AND space_id = %s AND uninstalled_at IS NULL RETURNING uninstalled_at",
            (instance_id, space_id),
        )
        row = await cur.fetchone()
        if row is None:
            raise NotFound("not_found")
        stamp = row["uninstalled_at"].astimezone(UTC).strftime("%Y%m%dT%H%M%S%fZ")
        return storage.trash_instance(space_id, access.space["gid"], instance_id, stamp)

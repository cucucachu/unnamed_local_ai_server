"""The app registry: apps, their versions, and instances installed in spaces (docs/PLATFORM.md §7).

- An app's source is exactly `/<space>/Apps/<slug>/` (`/personal/Apps/<slug>`
  or `/spaces/<s>/Apps/<slug>`); registering needs `write` on that space, a
  package that passes `manifest.validate_package`, and `app.json`'s `slug`
  equal to the folder name. The space's `Apps` folder is created if missing.
- Visible to a user: apps whose source space they can read, apps with a live
  instance in a space they belong to, and apps listed in a catalog of a space
  they belong to. Source (`source_path`, the working version) stays hidden
  unless they can read the source space.
- Installing needs `write` on the target space and a visible app. `tracks`
  is `working` (only in the app's own source space, D15) or a published
  version's id. A pinned install outside the source space also needs the app
  in that space's catalog. Each install gets `apps/<instance_id>/` in the
  target space; uninstalling moves it to `apps/.trash/<instance_id>-<stamp>/`.
- Publishing (write on the source space) snapshots the package, copies the
  working version's bundle into a `published` row, and lists the app in the
  chosen spaces' catalogs. Updates pin a newer published version (permission
  diffs must be re-granted). Fork copies the snapshot (or live source) into
  another space as a new app.

Agent delegations have their user's rights here (D6): none of this is an
admin or auth action.
"""

from __future__ import annotations

import errno
import json
import os
import re
import shutil
import stat
from datetime import UTC
from pathlib import Path
from typing import Any
from uuid import UUID

import anyio.to_thread
from psycopg import AsyncConnection
from psycopg.errors import UniqueViolation
from psycopg.types.json import Jsonb

from app.core import appschema, beneath, fsops, manifest, spaces, vfs
from app.core.errors import Conflict, InvalidApp, InvalidInput, NotFound, ServerError
from app.core.principal import Principal
from app.core.storage import SpaceStorage

Row = dict[str, Any]

WORKING = "working"
RELEASES_DIR = "app-releases"
SNAPSHOT_RE = re.compile(r"^app-releases/[0-9a-f-]{36}/[0-9a-f-]{36}$")
_OPEN_DIR = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
_OPEN_NEW = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC
MAX_SNAPSHOT_FILE_BYTES = 2 * 1024 * 1024
MAX_SNAPSHOT_BYTES = 16 * 1024 * 1024

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
  AND (sm.user_id IS NOT NULL
       OR EXISTS (
      SELECT 1 FROM app_instances i
      JOIN spaces isp ON isp.id = i.space_id AND isp.archived_at IS NULL
      JOIN space_members im ON im.space_id = i.space_id AND im.user_id = %(user)s
      WHERE i.app_id = a.id AND i.uninstalled_at IS NULL)
       OR EXISTS (
      SELECT 1 FROM app_catalog c
      JOIN spaces cs ON cs.id = c.space_id AND cs.archived_at IS NULL
      JOIN space_members cm ON cm.space_id = c.space_id AND cm.user_id = %(user)s
      WHERE c.app_id = a.id))
"""

_INSTANCES = """
SELECT i.id, i.app_id, i.space_id,
       CASE WHEN i.tracks = 'working' THEN 'working' ELSE i.version_id::text END AS tracks,
       i.installed_by, i.granted_permissions, i.granted_reads, i.created_at,
       json_build_object('id', a.id, 'slug', a.slug, 'name', a.name, 'version', v.version,
                         'icon', v.manifest -> 'homeai' ->> 'icon') AS app,
       CASE WHEN i.tracks = 'working' THEN NULL ELSE (
           SELECT json_build_object(
               'id', n.id, 'version', n.version,
               'permissions', COALESCE(n.manifest -> 'homeai' -> 'permissions', '{}'::jsonb))
           FROM app_versions n
           WHERE n.app_id = i.app_id AND n.kind = 'published'
             AND n.published_at > v.published_at
           ORDER BY n.published_at DESC, n.id
           LIMIT 1
       ) END AS update
FROM app_instances i
JOIN apps a ON a.id = i.app_id
LEFT JOIN app_versions v ON v.id = i.version_id
    OR (i.version_id IS NULL AND v.app_id = i.app_id AND v.kind = 'working')
WHERE i.uninstalled_at IS NULL
"""

_CATALOG = f"""
SELECT DISTINCT ON (c.app_id)
       a.id AS app_id, a.slug, a.name,
       {_VERSION_JSON.format(v="v")} AS version,
       (SELECT i.id FROM app_instances i
        WHERE i.app_id = a.id AND i.space_id = %(space)s AND i.uninstalled_at IS NULL
        LIMIT 1) AS instance_id
FROM app_catalog c
JOIN apps a ON a.id = c.app_id AND a.archived_at IS NULL
JOIN app_versions v ON v.app_id = a.id AND v.kind = 'published'
WHERE c.space_id = %(space)s
ORDER BY c.app_id, v.published_at DESC, v.id
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


async def resolve_source(
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


def open_source(r: vfs.Resolved) -> int | None:
    """An fd on the app folder (the caller closes it), or None if there is none."""
    with r.open_root() as root:
        try:
            return beneath.open(root, r.rel, os.O_RDONLY | os.O_DIRECTORY, symlinks=False)
        except (FileNotFoundError, NotADirectoryError):
            return None
        except OSError as exc:
            if exc.errno != errno.ELOOP:
                raise
            raise InvalidInput("invalid_source_path") from exc


def _validate_source(r: vfs.Resolved, slug: str) -> tuple[Any, list[manifest.Diagnostic]]:
    fd = open_source(r)
    try:
        return manifest.validate_package(fd, slug)
    finally:
        if fd is not None:
            os.close(fd)


def _diagnostics(found: list[manifest.Diagnostic]) -> list[dict[str, str]]:
    return [d.as_dict() for d in found]


def _permissions(doc: Any) -> dict[str, Any]:
    homeai = doc.get("homeai") if isinstance(doc, dict) else None
    perms = homeai.get("permissions") if isinstance(homeai, dict) else None
    return perms if isinstance(perms, dict) else {}


def _reads(doc: Any) -> list[Any]:
    return manifest.homeai_reads(doc)


def _reads_key(reads: list[Any]) -> list[tuple[Any, ...]]:
    keys = []
    for item in reads:
        if isinstance(item, dict):
            keys.append((item.get("app"), item.get("export"), item.get("version")))
        else:
            keys.append((item,))
    return sorted(keys)


def _grant(doc: Any, granted: dict[str, Any] | None, *, required: bool) -> dict[str, Any]:
    """The permissions recorded on an install or update.

    Empty permissions are granted automatically. A non-empty set must be sent
    back as `granted_permissions` (the install/update prompt).
    """
    wanted = _permissions(doc)
    if granted is None:
        if wanted and required:
            raise InvalidInput("permissions_required")
        return wanted
    if granted != wanted:
        raise InvalidInput("permissions_mismatch")
    return wanted


def _grant_reads(doc: Any, granted: list[Any] | None, *, required: bool) -> list[Any]:
    """The reads recorded on an install or update.

    Empty `homeai.reads` is granted automatically. A non-empty list must be sent
    back as `granted_reads` (the install/update prompt).
    """
    wanted = _reads(doc)
    if granted is None:
        if wanted and required:
            raise InvalidInput("reads_required")
        return wanted
    if _reads_key(granted) != _reads_key(wanted):
        raise InvalidInput("reads_mismatch")
    return wanted


# --- package snapshots -------------------------------------------------------------


def open_snapshot(data_dir: Path, rel: str) -> int:
    """An fd on a published package snapshot; the caller closes it."""
    if not SNAPSHOT_RE.fullmatch(rel):
        raise ServerError("release_invalid")
    fd = os.open(data_dir, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        for name in rel.split("/"):
            nxt = os.open(name, _OPEN_DIR, dir_fd=fd)
            os.close(fd)
            fd = nxt
        return fd
    except OSError:
        os.close(fd)
        raise


def read_snapshot_schema(data_dir: Path, rel: str) -> str | None:
    """`schema.sql` from a published snapshot, or None if it can't be read."""
    try:
        fd = open_snapshot(data_dir, rel)
    except (OSError, ServerError):
        return None
    try:
        with fsops.open_regular_at(fd, "schema.sql") as f:
            data = f.read(appschema.MAX_SCHEMA_BYTES + 1)
    except OSError:
        return None
    finally:
        os.close(fd)
    if len(data) > appschema.MAX_SCHEMA_BYTES:
        return None
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return None


def _copy_entry(
    src_dir: int, name: str, dst_dir: int, owner: fsops.Owner | None, tally: list[int]
) -> None:
    """Copy one package entry. `tally` is `[entries, bytes]`; raise InvalidApp on a bad entry."""
    try:
        st = os.stat(name, dir_fd=src_dir, follow_symlinks=False)
    except OSError as exc:
        raise InvalidApp([{"file": name, "path": "", "message": f"{name} can't be read"}]) from exc
    tally[0] += 1
    if tally[0] > manifest.MAX_PACKAGE_ENTRIES:
        raise InvalidApp(
            [
                {
                    "file": "",
                    "path": "",
                    "message": f"the app has more than {manifest.MAX_PACKAGE_ENTRIES} files",
                }
            ]
        )
    if stat.S_ISLNK(st.st_mode):
        raise InvalidApp(
            [{"file": name, "path": "", "message": f"{name} is a symlink; apps can't use symlinks"}]
        )
    if stat.S_ISREG(st.st_mode):
        try:
            fin = fsops.open_regular_at(src_dir, name)
        except OSError as exc:
            raise InvalidApp(
                [{"file": name, "path": "", "message": f"{name} can't be read as a regular file"}]
            ) from exc
        with fin:
            data = fin.read(MAX_SNAPSHOT_FILE_BYTES + 1)
        if len(data) > MAX_SNAPSHOT_FILE_BYTES:
            raise InvalidApp([{"file": name, "path": "", "message": f"{name} is larger than 2 MB"}])
        tally[1] += len(data)
        if tally[1] > MAX_SNAPSHOT_BYTES:
            raise InvalidApp(
                [{"file": "", "path": "", "message": "the app is larger than 16 MB in total"}]
            )
        flags, mode = _OPEN_NEW, 0o644
        fd = os.open(name, flags, mode, dir_fd=dst_dir)
        try:
            if owner is not None:
                fsops._adopt(fd, owner, fsops.file_mode_for(st.st_mode))
            with os.fdopen(fd, "wb", closefd=False) as fout:
                fout.write(data)
        finally:
            os.close(fd)
        return
    if not stat.S_ISDIR(st.st_mode):
        raise InvalidApp(
            [{"file": name, "path": "", "message": f"{name} isn't a file or a folder"}]
        )
    if owner is None:
        os.mkdir(name, 0o755, dir_fd=dst_dir)
        sub = os.open(name, _OPEN_DIR, dir_fd=dst_dir)
        os.fchmod(sub, 0o755)
    else:
        sub = fsops._mkdir_at(dst_dir, name, owner)
    src = os.open(name, _OPEN_DIR, dir_fd=src_dir)
    try:
        _copy_children(src, sub, owner, tally)
    finally:
        os.close(src)
        os.close(sub)


def _copy_children(src: int, dst: int, owner: fsops.Owner | None, tally: list[int]) -> None:
    with os.scandir(src) as it:
        names = sorted(e.name for e in it if not e.name.startswith("."))
    for name in names:
        _copy_entry(src, name, dst, owner, tally)


def _snapshot_package(data_dir: Path, pkg_fd: int, app_id: UUID, version_id: UUID) -> str:
    rel = f"{RELEASES_DIR}/{app_id}/{version_id}"
    dest = data_dir / rel
    dest.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    dest.mkdir(mode=0o700)
    dst = os.open(dest, _OPEN_DIR)
    try:
        os.fchmod(dst, 0o700)
        _copy_children(pkg_fd, dst, None, [0, 0])
    except Exception:
        os.close(dst)
        shutil.rmtree(dest, ignore_errors=True)
        raise
    os.close(dst)
    return rel


def _package_exists(r: vfs.Resolved, slug: str) -> bool:
    with r.open_root() as root:
        try:
            beneath.lstat_at(root, (vfs.APPS, slug))
        except FileNotFoundError:
            return False
        except (NotADirectoryError, OSError) as exc:
            if isinstance(exc, OSError) and exc.errno == errno.ELOOP:
                raise InvalidInput("invalid_source_path") from exc
            raise
    return True


def _copy_into_space(r: vfs.Resolved, slug: str, src_fd: int, owner: fsops.Owner) -> None:
    try:
        with r.open_root() as root:
            fsops.make_dirs(root, (vfs.APPS, slug), owner)
            dest = beneath.open(
                root, (vfs.APPS, slug), os.O_RDONLY | os.O_DIRECTORY, symlinks=False
            )
            try:
                _copy_children(src_fd, dest, owner, [0, 0])
            finally:
                os.close(dest)
    except (FileExistsError, NotADirectoryError) as exc:
        raise Conflict("apps_folder_not_a_directory") from exc


def _rewrite_slug(r: vfs.Resolved, slug: str, owner: fsops.Owner) -> None:
    with r.open_root() as root:
        dir_fd = beneath.open(root, (vfs.APPS, slug), os.O_RDONLY | os.O_DIRECTORY, symlinks=False)
        try:
            with fsops.open_regular_at(dir_fd, "app.json") as f:
                doc = json.loads(f.read())
            if not isinstance(doc, dict):
                raise InvalidApp(
                    [{"file": "app.json", "path": "", "message": "app.json is not an object"}]
                )
            doc["slug"] = slug
            fsops.replace_file_at(
                dir_fd, "app.json", json.dumps(doc, indent=2).encode() + b"\n", owner
            )
        finally:
            os.close(dir_fd)


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
    r, slug = await resolve_source(conn, principal, storage, source_path)
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
    r, slug = await resolve_source(conn, principal, storage, app["source_path"])
    doc, found = await anyio.to_thread.run_sync(_validate_source, r, slug)
    if doc is not None and app.get("working_version"):
        fd = open_source(r)
        try:
            new_schema = manifest.read_schema_sql(fd) if fd is not None else None
        finally:
            if fd is not None:
                os.close(fd)
        found = list(found) + manifest.export_contract_diagnostics(
            app["working_version"]["manifest"], None, doc, new_schema
        )
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


async def _in_catalog(conn: AsyncConnection, app_id: UUID, space_id: UUID) -> bool:
    cur = await conn.execute(
        "SELECT 1 FROM app_catalog WHERE app_id = %s AND space_id = %s", (app_id, space_id)
    )
    return await cur.fetchone() is not None


async def install_app(
    conn: AsyncConnection,
    principal: Principal,
    storage: SpaceStorage,
    space_id: UUID,
    app_id: UUID,
    tracks: str,
    granted_permissions: dict[str, Any] | None = None,
    granted_reads: list[Any] | None = None,
) -> Row:
    access = await spaces.authorize_space(conn, principal, space_id, "write")
    app = await get_visible_app(conn, principal, app_id)
    if tracks == WORKING:
        if app["source_space_id"] != space_id:
            raise InvalidInput("working_requires_source_space")
        if app["working_version"] is None:
            raise NotFound("not_found")
        version_id, doc = None, app["working_version"]["manifest"]
        required = False
    else:
        pinned = await _pinned_version(conn, app_id, tracks)
        if app["source_space_id"] != space_id and not await _in_catalog(conn, app_id, space_id):
            raise InvalidInput("not_in_catalog")
        version_id, doc = pinned["id"], pinned["manifest"]
        required = True
    granted = _grant(doc, granted_permissions, required=required)
    reads = _grant_reads(doc, granted_reads, required=required)
    try:
        async with conn.transaction():
            cur = await conn.execute(
                "INSERT INTO app_instances "
                "(app_id, space_id, tracks, version_id, installed_by, "
                "granted_permissions, granted_reads) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id",
                (
                    app_id, space_id, WORKING if version_id is None else "version",
                    version_id, principal.user_id, Jsonb(granted), Jsonb(reads),
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


# --- publish / catalog / update / fork (M14-01) --------------------------------------


async def _working_for_publish(conn: AsyncConnection, app_id: UUID) -> Row:
    cur = await conn.execute(
        "SELECT id, version, commit, manifest, bundle_path FROM app_versions "
        "WHERE app_id = %s AND kind = 'working'",
        (app_id,),
    )
    row = await cur.fetchone()
    if row is None or not row["bundle_path"]:
        raise InvalidInput("not_built")
    return row


def _snapshot_from_source(data_dir: Path, r: vfs.Resolved, app_id: UUID, version_id: UUID) -> str:
    fd = open_source(r)
    if fd is None:
        raise InvalidApp([{"file": "", "path": "", "message": "the app folder is missing"}])
    try:
        return _snapshot_package(data_dir, fd, app_id, version_id)
    finally:
        os.close(fd)


async def publish_app(
    conn: AsyncConnection,
    principal: Principal,
    storage: SpaceStorage,
    data_dir: Path,
    app_id: UUID,
    space_ids: list[UUID],
) -> tuple[Row, Row, list[UUID]]:
    """Publish the working version into `space_ids`' catalogs. Needs write on the source
    and on each catalog space."""
    app = await get_visible_app(conn, principal, app_id)
    await spaces.authorize_space(conn, principal, app["source_space_id"], "write")
    if app["source_path"] is None:
        raise NotFound("not_found")
    working = await _working_for_publish(conn, app_id)
    seen: list[UUID] = []
    for space_id in space_ids:
        if space_id in seen:
            continue
        await spaces.authorize_space(conn, principal, space_id, "write")
        seen.append(space_id)
    r, _slug = await resolve_source(conn, principal, storage, app["source_path"])
    try:
        async with conn.transaction():
            cur = await conn.execute(
                "INSERT INTO app_versions "
                "(app_id, version, kind, commit, manifest, bundle_path, published_at) "
                "VALUES (%s, %s, 'published', %s, %s, %s, now()) RETURNING id",
                (
                    app_id, working["version"], working["commit"],
                    Jsonb(working["manifest"]), working["bundle_path"],
                ),
            )  # fmt: skip
            version_id = (await cur.fetchone())["id"]
            snapshot = await anyio.to_thread.run_sync(
                _snapshot_from_source, data_dir, r, app_id, version_id
            )
            await conn.execute(
                "UPDATE app_versions SET source_snapshot = %s WHERE id = %s",
                (snapshot, version_id),
            )
            for space_id in seen:
                await conn.execute(
                    "INSERT INTO app_catalog (app_id, space_id, listed_by) VALUES (%s, %s, %s) "
                    "ON CONFLICT (app_id, space_id) DO NOTHING",
                    (app_id, space_id, principal.user_id),
                )
    except UniqueViolation as exc:
        if exc.diag.constraint_name == "app_versions_published_key":
            raise Conflict("version_exists") from exc
        raise
    cur = await conn.execute(
        "SELECT id, version, kind, commit, manifest, bundle_path, created_at, published_at "
        "FROM app_versions WHERE id = %s",
        (version_id,),
    )
    version = await cur.fetchone()
    return await get_visible_app(conn, principal, app_id), version, seen


def _catalog_entry(row: Row) -> Row:
    version = row["version"]
    icon = None
    manifest_doc = version.get("manifest") if isinstance(version, dict) else None
    if isinstance(manifest_doc, dict):
        homeai = manifest_doc.get("homeai")
        if isinstance(homeai, dict):
            icon = homeai.get("icon")
    return {
        "app": {
            "id": row["app_id"],
            "slug": row["slug"],
            "name": row["name"],
            "version": version.get("version") if isinstance(version, dict) else None,
            "icon": icon,
        },
        "version": version,
        "installed": row["instance_id"] is not None,
        "instance_id": row["instance_id"],
    }


async def list_catalog(conn: AsyncConnection, principal: Principal, space_id: UUID) -> list[Row]:
    await spaces.authorize_space(conn, principal, space_id, "read")
    cur = await conn.execute(_CATALOG, {"space": space_id})
    entries = [_catalog_entry(row) for row in await cur.fetchall()]
    entries.sort(
        key=lambda e: (str(e["app"]["name"]).lower(), e["app"]["slug"], str(e["app"]["id"]))
    )
    return entries


async def _get_published(conn: AsyncConnection, app_id: UUID, version_id: UUID) -> Row:
    cur = await conn.execute(
        "SELECT id, version, kind, commit, manifest, bundle_path, source_snapshot, "
        "created_at, published_at FROM app_versions "
        "WHERE id = %s AND app_id = %s AND kind = 'published'",
        (version_id, app_id),
    )
    row = await cur.fetchone()
    if row is None:
        raise InvalidInput("unknown_version")
    return row


async def update_instance(
    conn: AsyncConnection,
    principal: Principal,
    space_id: UUID,
    instance_id: UUID,
    version_id: UUID,
    granted_permissions: dict[str, Any] | None,
    granted_reads: list[Any] | None = None,
) -> Row:
    """Pin a live instance to a newer published version. Write on the space."""
    await spaces.authorize_space(conn, principal, space_id, "write")
    inst = await _get_instance(conn, instance_id)
    if inst is None or inst["space_id"] != space_id:
        raise NotFound("not_found")
    if inst["tracks"] == WORKING:
        raise InvalidInput("working_not_updatable")
    target = await _get_published(conn, inst["app_id"], version_id)
    if str(target["id"]) == inst["tracks"]:
        raise Conflict("already_on_version")
    wanted = _permissions(target["manifest"])
    if granted_permissions is not None and granted_permissions != wanted:
        raise InvalidInput("permissions_mismatch")
    if wanted != inst["granted_permissions"] and granted_permissions is None:
        raise InvalidInput("permissions_changed")
    wanted_reads = _reads(target["manifest"])
    if granted_reads is not None and _reads_key(granted_reads) != _reads_key(wanted_reads):
        raise InvalidInput("reads_mismatch")
    stored_reads = inst["granted_reads"] if isinstance(inst.get("granted_reads"), list) else []
    if _reads_key(wanted_reads) != _reads_key(stored_reads) and granted_reads is None:
        raise InvalidInput("reads_changed")
    granted = wanted
    async with conn.transaction():
        await conn.execute(
            "UPDATE app_instances SET version_id = %s, granted_permissions = %s, "
            "granted_reads = %s WHERE id = %s AND uninstalled_at IS NULL",
            (version_id, Jsonb(granted), Jsonb(wanted_reads), instance_id),
        )
    updated = await _get_instance(conn, instance_id)
    if updated is None:
        raise NotFound("not_found")
    return updated


async def fork_app(
    conn: AsyncConnection,
    principal: Principal,
    storage: SpaceStorage,
    data_dir: Path,
    app_id: UUID,
    space_id: UUID,
    slug: str | None,
) -> tuple[Row, Row]:
    """Copy the app's source (live if readable, else the latest published snapshot)
    into `space_id` as a new app, and install it tracking `working`."""
    app = await get_visible_app(conn, principal, app_id)
    dest = await spaces.authorize_space(conn, principal, space_id, "write")
    slug = slug or app["slug"]
    if not spaces.SLUG_RE.fullmatch(slug):
        raise InvalidInput("invalid_source_path")
    dest_path = f"{vfs.space_prefix(dest.space)}/{vfs.APPS}/{slug}"
    r = await vfs.resolve_virtual_path(conn, principal, storage, dest_path, "write")
    owner = fsops.Owner(principal.uid, dest.space["gid"])
    cur = await conn.execute(
        "SELECT 1 FROM apps WHERE source_space_id = %s AND slug = %s AND archived_at IS NULL",
        (space_id, slug),
    )
    if await cur.fetchone() is not None:
        raise Conflict("app_exists")
    if await anyio.to_thread.run_sync(_package_exists, r, slug):
        raise Conflict("app_exists")

    src_fd: int | None = None
    if app["source_path"] is not None:
        src_r = await vfs.resolve_virtual_path(conn, principal, storage, app["source_path"], "read")
        src_fd = await anyio.to_thread.run_sync(open_source, src_r)
    if src_fd is None:
        cur = await conn.execute(
            "SELECT source_snapshot FROM app_versions "
            "WHERE app_id = %s AND kind = 'published' AND source_snapshot IS NOT NULL "
            "ORDER BY published_at DESC, id DESC LIMIT 1",
            (app_id,),
        )
        snap = await cur.fetchone()
        if snap is None:
            raise InvalidInput("not_published")
        try:
            src_fd = await anyio.to_thread.run_sync(
                open_snapshot, data_dir, snap["source_snapshot"]
            )
        except OSError as exc:
            raise ServerError("release_invalid") from exc
    try:
        await anyio.to_thread.run_sync(_copy_into_space, r, slug, src_fd, owner)
        if slug != app["slug"]:
            await anyio.to_thread.run_sync(_rewrite_slug, r, slug, owner)
    finally:
        os.close(src_fd)

    forked = await register_app(conn, principal, storage, dest_path)
    instance = await install_app(conn, principal, storage, space_id, forked["id"], WORKING)
    return forked, instance

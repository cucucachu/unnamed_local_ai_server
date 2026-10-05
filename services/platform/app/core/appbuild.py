"""App builds (docs/PLATFORM.md §7 "Build and verify"; docs/ARCHITECTURE.md §3 "App builds").

`build_app` copies the app's source into a staging dir the platform owns,
checks the copy (`manifest.validate_package`, step `manifest`), has
code-exec-manager run the builder image over it (`compile`, then `smoke`),
and on success stores the bundle as the working version's `bundle_path`.

    <builds root>/                  root 0700 (host: ${APP_BUILDS_DIR})
    <builds root>/<build_id>/       root 0700
        src/                        root 0755, files 0644: the copied package
        bundle/                     19999 0700: the compile phase's output
        smoke/                      19999 0700: the smoke phase's output

The source tree is writable by the space's members, so it is never mounted
itself: the copy walks it by fd, `O_NOFOLLOW` all the way, and refuses
symlinks and anything that isn't a file or folder. What gets validated and
built is that copy, so edits made meanwhile can't slip past the checks.
Everything under `bundle/` and `smoke/` was written by a build container and
is read the same way, with size caps, as untrusted input.

    <platform data>/app-bundles/<app_id>/<build_id>/app.js(.map)

is where a successful build's bundle goes; `bundle_path` is that path
relative to the platform data dir. A failed build leaves the previous one
in place; archiving the app's source space removes it
(`release_space_bundles`).

A successful build also commits the staged `src/` to the app's history
(`app.core.apphistory`) and records the commit as the working version's
`commit`; a failed one commits nothing. `revert_app` commits an earlier
tree again, writes it back into the source folder by fd, and rebuilds.

A successful build whose `app.json` version isn't above every published
version, and whose source differs from the latest published one, builds as
the next patch after the highest published version: the staged `app.json`
is rewritten before the commit and then written back into the source folder
(skipped if that file changed meanwhile). Major and minor bumps are the
author's.
"""

from __future__ import annotations

import errno
import json
import logging
import os
import re
import secrets
import shutil
import stat
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol
from uuid import UUID

import anyio.to_thread
import httpx
from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool

from app.core import appdb, apphistory, apps, appschema, fsops, manifest, spaces, vfs
from app.core.appdata import AppData
from app.core.apphistory import AppHistory, HistoryError
from app.core.errors import InvalidInput, NotFound, ServerError, Unavailable
from app.core.principal import Principal
from app.core.storage import SpaceStorage

logger = logging.getLogger(__name__)

BUILDER_UID = 19999
PHASES = ("compile", "smoke")
STEPS = ("manifest", "files", "route", "import", "bundle", "type", "render", "sql", "build")

MAX_FILE_BYTES = 2 * 1024 * 1024
MAX_SOURCE_BYTES = 16 * 1024 * 1024
MAX_RESULT_BYTES = 1024 * 1024
MAX_BUNDLE_BYTES = 16 * 1024 * 1024
MAX_MAP_BYTES = 32 * 1024 * 1024
MAX_FIELD_CHARS = {"file": 500, "message": 2000, "source": 200}

BUNDLES_DIR = "app-bundles"
BUNDLE_PREFIX = b"__homeai_define("
BUNDLE_PATH_RE = re.compile(r"^app-bundles/[0-9a-f-]{36}/[0-9a-f]{32}/app\.js$")

Diagnostic = dict[str, Any]

_OPEN_DIR = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
_OPEN_NEW = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC


def _fchown(fd: int, uid: int, gid: int) -> None:
    os.fchown(fd, uid, gid)


def diagnostic(step: str, file: str = "", message: str = "", path: str = "") -> Diagnostic:
    """`{step, file, path, line, column, message}`, `manifest.Diagnostic` plus a step and position."""
    return {"step": step, "file": file, "path": path, "line": None, "column": None,
            "message": message}  # fmt: skip


def _mb(n: int) -> str:
    return f"{n // (1024 * 1024)} MB"


# --- staging -----------------------------------------------------------------------


class _TooMany(Exception):
    pass


@dataclass
class _Copy:
    entries: int = 0
    bytes: int = 0
    found: list[Diagnostic] = field(default_factory=list)

    def refuse(self, rel: str, message: str) -> None:
        self.found.append(diagnostic("files", rel, message))


def _copy_file(src_dir: int, name: str, dst_dir: int, rel: str, copy: _Copy) -> None:
    try:
        fin = fsops.open_regular_at(src_dir, name)
    except OSError:
        copy.refuse(rel, f"{rel} can't be read as a regular file; remove it")
        return
    with fin:
        data = fin.read(MAX_FILE_BYTES + 1)
    if len(data) > MAX_FILE_BYTES:
        copy.refuse(
            rel, f"{rel} is larger than {_mb(MAX_FILE_BYTES)}; apps can't use files that big"
        )
        return
    copy.bytes += len(data)
    if copy.bytes > MAX_SOURCE_BYTES:
        copy.refuse(rel, f"the app is larger than {_mb(MAX_SOURCE_BYTES)} in total")
        raise _TooMany
    with os.fdopen(os.open(name, _OPEN_NEW, 0o644, dir_fd=dst_dir), "wb") as fout:
        fout.write(data)


def _copy_tree(src: int, dst: int, rel: str, copy: _Copy) -> None:
    with os.scandir(src) as it:
        entries = sorted(it, key=lambda e: e.name)
    for entry in entries:
        if entry.name.startswith("."):
            continue
        copy.entries += 1
        if copy.entries > manifest.MAX_PACKAGE_ENTRIES:
            copy.refuse("", f"the app has more than {manifest.MAX_PACKAGE_ENTRIES} files")
            raise _TooMany
        child = f"{rel}{entry.name}"
        mode = entry.stat(follow_symlinks=False).st_mode
        if stat.S_ISLNK(mode):
            copy.refuse(child, f"{child} is a symlink; apps can't use symlinks")
        elif stat.S_ISREG(mode):
            _copy_file(src, entry.name, dst, child, copy)
        elif stat.S_ISDIR(mode):
            try:
                fd = os.open(entry.name, _OPEN_DIR, dir_fd=src)
            except OSError:
                copy.refuse(child, f"{child}/ can't be read; remove it")
                continue
            try:
                os.mkdir(entry.name, 0o755, dir_fd=dst)
                sub = os.open(entry.name, _OPEN_DIR, dir_fd=dst)
                try:
                    os.fchmod(sub, 0o755)
                    _copy_tree(fd, sub, f"{child}/", copy)
                finally:
                    os.close(sub)
            finally:
                os.close(fd)
        else:
            copy.refuse(child, f"{child} isn't a file or a folder; remove it")


class Builds:
    """The staging dirs under `root` (the platform's `/data/builds`)."""

    def __init__(self, root: Path) -> None:
        self.root = root

    def prepare(self) -> None:
        """Make the root platform-only and empty it of builds a restart interrupted."""
        self.root.mkdir(mode=0o700, exist_ok=True)
        fd = os.open(self.root, _OPEN_DIR)
        try:
            os.fchmod(fd, 0o700)
            with os.scandir(fd) as it:
                stale = [entry.name for entry in it]
            for name in stale:
                fsops.remove_at(fd, name)
        finally:
            os.close(fd)
        if stale:
            logger.info("builds: removed %d stale staging dirs", len(stale))

    def _open(self, *parts: str) -> int:
        fd = os.open(self.root, _OPEN_DIR)
        try:
            for part in parts:
                nxt = os.open(part, _OPEN_DIR, dir_fd=fd)
                os.close(fd)
                fd = nxt
        except BaseException:
            os.close(fd)
            raise
        return fd

    def stage(self, pkg_fd: int, slug: str) -> tuple[str, Any, list[Diagnostic]]:
        """Copy the package open as `pkg_fd` into a new build dir and validate the copy.

        (build_id, manifest, diagnostics); the build dir exists either way.
        """
        build_id = secrets.token_hex(16)
        root = self._open()
        try:
            os.mkdir(build_id, 0o700, dir_fd=root)
        finally:
            os.close(root)
        build = self._open(build_id)
        try:
            for name in ("bundle", "smoke"):
                os.mkdir(name, 0o700, dir_fd=build)
                fd = os.open(name, _OPEN_DIR, dir_fd=build)
                try:
                    _fchown(fd, BUILDER_UID, BUILDER_UID)
                    os.fchmod(fd, 0o700)
                finally:
                    os.close(fd)
            os.mkdir("src", 0o755, dir_fd=build)
            src = os.open("src", _OPEN_DIR, dir_fd=build)
        finally:
            os.close(build)
        copy = _Copy()
        try:
            os.fchmod(src, 0o755)
            try:
                _copy_tree(pkg_fd, src, "", copy)
            except _TooMany:
                pass
            if copy.found:
                return build_id, None, copy.found[: manifest.MAX_DIAGNOSTICS]
            doc, found = manifest.validate_package(src, slug)
        finally:
            os.close(src)
        return build_id, doc, [diagnostic("manifest", d.file, d.message, d.path) for d in found]

    def read(self, build_id: str, out_dir: str, name: str, limit: int) -> bytes | None:
        """`<build_id>/<out_dir>/<name>` if it is a regular file of at most `limit` bytes."""
        try:
            dir_fd = self._open(build_id, out_dir)
        except OSError:
            return None
        try:
            with fsops.open_regular_at(dir_fd, name) as f:
                data = f.read(limit + 1)
        except OSError:
            return None
        finally:
            os.close(dir_fd)
        return data if len(data) <= limit else None

    def discard(self, build_id: str) -> None:
        fd = self._open()
        try:
            fsops.remove_at(fd, build_id)
        except FileNotFoundError:
            pass
        finally:
            os.close(fd)


# --- the builder ----------------------------------------------------------------------


@dataclass(frozen=True)
class PhaseRun:
    exit_code: int
    timed_out: bool


class Builder(Protocol):
    async def run(self, build_id: str, phase: str) -> PhaseRun: ...


class ExecManagerBuilder:
    """`POST {exec_manager_url}/builds/{build_id}/{phase}` with the build token."""

    def __init__(
        self,
        base_url: str,
        token: str,
        timeout_s: float,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._url = base_url.rstrip("/")
        self._token = token
        self._timeout_s = timeout_s
        self._transport = transport

    async def run(self, build_id: str, phase: str) -> PhaseRun:
        if not self._token:
            raise Unavailable("builder_unavailable")
        try:
            async with httpx.AsyncClient(
                timeout=self._timeout_s, transport=self._transport
            ) as client:
                response = await client.post(
                    f"{self._url}/builds/{build_id}/{phase}",
                    headers={"Authorization": f"Bearer {self._token}"},
                )
            if response.status_code != 200:
                logger.warning("builds: code-exec-manager answered %d", response.status_code)
                raise Unavailable("builder_unavailable")
            body = response.json()
            return PhaseRun(int(body["exit_code"]), bool(body["timed_out"]))
        except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
            logger.warning("builds: code-exec-manager call failed: %s", type(exc).__name__)
            raise Unavailable("builder_unavailable") from exc


# --- results ---------------------------------------------------------------------------


def _clean(raw: Any) -> Diagnostic | None:
    if not isinstance(raw, dict) or not isinstance(raw.get("message"), str):
        return None
    d = diagnostic(raw["step"] if raw.get("step") in STEPS else "build")
    for key in ("file", "message"):
        value = raw.get(key)
        d[key] = value[: MAX_FIELD_CHARS[key]] if isinstance(value, str) else ""
    for key in ("line", "column"):
        value = raw.get(key)
        if isinstance(value, int) and not isinstance(value, bool) and 0 < value < 10_000_000:
            d[key] = value
    if isinstance(raw.get("source"), str) and raw["source"]:
        d["source"] = raw["source"][: MAX_FIELD_CHARS["source"]]
    return d


def _result(builds: Builds, build_id: str, phase: str, run: PhaseRun) -> list[Diagnostic]:
    """A phase's diagnostics from the `result.json` it wrote; empty if it succeeded."""
    if run.timed_out:
        return [diagnostic("build", message=f"the {phase} step took too long and was stopped")]
    raw = builds.read(build_id, "bundle" if phase == "compile" else "smoke", "result.json",
                      MAX_RESULT_BYTES)  # fmt: skip
    try:
        doc = json.loads(raw) if raw is not None else None
    except ValueError:
        doc = None
    if not isinstance(doc, dict) or not isinstance(doc.get("diagnostics"), list):
        logger.warning("builds: %s phase left no usable result (exit %d)", phase, run.exit_code)
        return [diagnostic("build", message=f"the {phase} step failed without a result")]
    found = [d for d in map(_clean, doc["diagnostics"][: manifest.MAX_DIAGNOSTICS]) if d]
    if found or (doc.get("ok") is True and run.exit_code == 0):
        return found
    return [diagnostic("build", message=f"the {phase} step failed")]


@dataclass(frozen=True)
class Build:
    id: str
    ok: bool
    duration_ms: int
    bundle_path: str | None = None
    bundle_bytes: int | None = None
    commit: str | None = None


def _bundle(builds: Builds, build_id: str) -> tuple[bytes, bytes] | None:
    bundle = builds.read(build_id, "bundle", "app.js", MAX_BUNDLE_BYTES)
    source_map = builds.read(build_id, "bundle", "app.js.map", MAX_MAP_BYTES)
    if bundle is None or source_map is None or not bundle.startswith(BUNDLE_PREFIX):
        return None
    return bundle, source_map


async def _run(builds: Builds, builder: Builder, build_id: str):
    """(diagnostics, (bundle, source map) or None)."""
    for phase in PHASES:
        run = await builder.run(build_id, phase)
        found = await anyio.to_thread.run_sync(_result, builds, build_id, phase, run)
        if found:
            return found, None
    output = await anyio.to_thread.run_sync(_bundle, builds, build_id)
    if output is None:
        return [diagnostic("build", message="the build produced no usable bundle")], None
    return [], output


# --- the build -----------------------------------------------------------------------------


def _staged_schema(builds: Builds, build_id: str) -> str | None:
    data = builds.read(build_id, "src", "schema.sql", appschema.MAX_SCHEMA_BYTES)
    try:
        return None if data is None else data.decode("utf-8")
    except UnicodeDecodeError:
        return None


def _stage(r: vfs.Resolved, slug: str, builds: Builds) -> tuple[str | None, Any, list[Diagnostic]]:
    fd = apps.open_source(r)
    if fd is None:
        _doc, found = manifest.validate_package(None, slug)
        return None, None, [diagnostic("manifest", d.file, d.message, d.path) for d in found]
    try:
        return builds.stage(fd, slug)
    finally:
        os.close(fd)


def _store_bundle(data_dir: Path, app_id: UUID, build_id: str, output: tuple[bytes, bytes]) -> str:
    rel = f"{BUNDLES_DIR}/{app_id}/{build_id}"
    target = data_dir / rel
    target.mkdir(mode=0o755, parents=True)
    (target / "app.js").write_bytes(output[0])
    (target / "app.js.map").write_bytes(output[1])
    return f"{rel}/app.js"


def _drop_bundle(data_dir: Path, bundle_path: str | None) -> None:
    if bundle_path and BUNDLE_PATH_RE.fullmatch(bundle_path):
        shutil.rmtree(data_dir / bundle_path.rsplit("/", 1)[0], ignore_errors=True)


async def release_space_bundles(conn, space_id: UUID) -> list[str]:
    """Unset the working bundles of the apps sourced in `space_id`; returns their paths.

    For `drop_bundles` once the caller's transaction commits. Published
    versions keep theirs: they can be installed in other spaces.
    """
    cur = await conn.execute(
        "SELECT v.id, v.bundle_path FROM app_versions v JOIN apps a ON a.id = v.app_id "
        "WHERE a.source_space_id = %s AND v.kind = 'working' AND v.bundle_path IS NOT NULL "
        "FOR UPDATE OF v",
        (space_id,),
    )
    rows = await cur.fetchall()
    if rows:
        await conn.execute(
            "UPDATE app_versions SET bundle_path = NULL WHERE id = ANY(%s)",
            ([row["id"] for row in rows],),
        )
    return [row["bundle_path"] for row in rows]


def drop_bundles(data_dir: Path, bundle_paths: list[str]) -> None:
    for bundle_path in bundle_paths:
        _drop_bundle(data_dir, bundle_path)
        if BUNDLE_PATH_RE.fullmatch(bundle_path):
            try:
                (data_dir / bundle_path).parent.parent.rmdir()
            except OSError:
                pass


def _staged_tree(history: AppHistory | None, app_id: UUID, builds: Builds, build_id: str):
    if history is None:
        return None
    try:
        return history.write_tree(app_id, builds.root / build_id / "src")
    except HistoryError as exc:
        logger.error("history: app %s: build %s not stored: %s", app_id, build_id, exc)
        return None


_CORE_RE = re.compile(r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)")


def _core(version: Any) -> tuple[int, int, int] | None:
    m = _CORE_RE.match(version) if isinstance(version, str) else None
    return (int(m[1]), int(m[2]), int(m[3])) if m else None


def _bumped_version(
    history: AppHistory | None, app_id: UUID, version: str, published: list, tree: str | None
) -> str | None:
    """The patch after the highest published version, if `version` isn't above it and the
    source differs from the latest published one (`published` is newest first); else None.

    So a publish never collides, and the builds between two publishes share one bump.
    """
    cores = [c for c in (_core(p["version"]) for p in published) if c]
    mine = _core(version)
    if not cores or mine is None or mine > max(cores):
        return None
    latest = published[0]["commit"]
    if (
        history is not None
        and tree is not None
        and latest
        and history.tree_of(app_id, latest) == tree
    ):
        return None
    major, minor, patch = max(cores)
    return f"{major}.{minor}.{patch + 1}"


def _set_staged_version(builds: Builds, build_id: str, version: str) -> tuple[bytes, bytes]:
    """Rewrite the staged `app.json` with `version`: (its bytes before, after)."""
    path = builds.root / build_id / "src" / manifest.MANIFEST_FILE
    before = path.read_bytes()
    doc = json.loads(before)
    doc["version"] = version
    after = (json.dumps(doc, indent=2, ensure_ascii=False) + "\n").encode()
    path.write_bytes(after)
    return before, after


def _write_back_version(r: vfs.Resolved, before: bytes, after: bytes, owner: fsops.Owner) -> bool:
    """Put the bumped `app.json` in the source folder, unless it changed since staging."""
    fd = apps.open_source(r)
    if fd is None:
        return False
    try:
        try:
            with fsops.open_regular_at(fd, manifest.MANIFEST_FILE) as f:
                current = f.read(len(before) + 1)
        except OSError:
            return False
        if current != before:
            return False
        fsops.replace_file_at(fd, manifest.MANIFEST_FILE, after, owner)
        return True
    finally:
        os.close(fd)


READS_FILE = "reads.sql"

# Instances of `slug` in the user's spaces, other than the app being built,
# with the commit of the version each one runs.
_EXPORTERS = """
SELECT DISTINCT a.id AS app_id, v.manifest, v.commit
FROM app_instances i
JOIN apps a ON a.id = i.app_id AND a.archived_at IS NULL
JOIN spaces s ON s.id = i.space_id AND s.archived_at IS NULL
JOIN space_members m ON m.space_id = s.id AND m.user_id = %(user)s
    AND (%(scope)s::uuid IS NULL OR s.id = %(scope)s)
JOIN app_versions v ON v.id = i.version_id
    OR (i.version_id IS NULL AND v.app_id = i.app_id AND v.kind = 'working')
WHERE i.uninstalled_at IS NULL AND a.slug = %(slug)s AND a.id <> %(app)s
  AND v.commit IS NOT NULL
"""


def _export_columns(
    history: AppHistory, app_id: UUID, commit: str, tables: list[str]
) -> dict[str, tuple[str, ...]] | None:
    try:
        files = history.read_tree(app_id, commit, manifest.MAX_PACKAGE_ENTRIES, MAX_SOURCE_BYTES)
    except HistoryError:
        return None
    try:
        cols = manifest.schema_table_columns(files.get("schema.sql", b"").decode("utf-8"))
    except UnicodeDecodeError:
        return None
    if cols is None or any(t not in cols for t in tables):
        return None
    return {t: cols[t] for t in tables}


async def _reads_stand_ins(
    pool: AsyncConnectionPool, principal: Principal, history: AppHistory | None, app_id: UUID,
    doc: Any,
) -> str:  # fmt: skip
    """`CREATE TABLE`s standing in for the merged views of `doc`'s `homeai.reads`, for the
    smoke render: each export's columns plus `_space`, taken from the schema an instance of
    the exporter visible to `principal` was built with. Reads with none are left out."""
    if history is None:
        return ""
    statements: list[str] = []
    for read in manifest.homeai_reads(doc):
        if not isinstance(read, dict):
            continue
        slug, name, version = read.get("app"), read.get("export"), read.get("version")
        async with pool.connection() as conn:
            cur = await conn.execute(
                _EXPORTERS,
                {
                    "user": principal.user_id,
                    "scope": principal.space_scope,
                    "slug": slug,
                    "app": app_id,
                },
            )
            rows = await cur.fetchall()
        for row in rows:
            exp = manifest.find_export(row["manifest"], name)
            if exp is None or str(exp.get("version")) != str(version):
                continue
            tables = [t for t in exp.get("tables") or [] if isinstance(t, str)]
            cols = await anyio.to_thread.run_sync(
                _export_columns, history, row["app_id"], row["commit"], tables
            )
            if not cols:
                continue
            for table in tables:
                view = appdb.merged_view_name(slug, name, table, tables)
                names = ", ".join(appschema.quote(c) for c in (*cols[table], "_space"))
                statements.append(f"CREATE TABLE {appschema.quote(view)} ({names});")
            break
    return "".join(f"{s}\n" for s in statements)


def _write_reads(builds: Builds, build_id: str, sql: str) -> None:
    fd = builds._open(build_id, "smoke")
    try:
        with os.fdopen(os.open(READS_FILE, _OPEN_NEW, 0o644, dir_fd=fd), "w") as f:
            f.write(sql)
    finally:
        os.close(fd)


def _thread_id(principal: Principal) -> str | None:
    return principal.thread_id if principal.is_agent else None


async def build_app(
    pool: AsyncConnectionPool,
    principal: Principal,
    storage: SpaceStorage,
    builds: Builds,
    builder: Builder,
    data_dir: Path,
    appdata: AppData,
    history: AppHistory | None,
    app_id: UUID,
) -> tuple[dict[str, Any], Build | None, list[Diagnostic], list[dict[str, Any]]]:
    """(app, the build or None if the builder never ran, diagnostics, instance migrations).

    Needs `write` on the source space. The database connection isn't held
    while the builder runs. A successful build commits what it built to the
    app's history (unless that is already the head), then migrates the
    instances that track the working version to the `schema.sql` it built
    (`AppData.built`), which also emits `app_built`. Without a usable
    history (logged) the build still succeeds, with no commit.
    """
    async with pool.connection() as conn:
        app = await apps.get_visible_app(conn, principal, app_id)
        await spaces.authorize_space(conn, principal, app["source_space_id"], "write")
        r, slug = await apps.resolve_source(conn, principal, storage, app["source_path"])
        cur = await conn.execute(
            "SELECT source_snapshot FROM app_versions "
            "WHERE app_id = %s AND kind = 'published' AND source_snapshot IS NOT NULL "
            "ORDER BY published_at DESC, id DESC LIMIT 1",
            (app_id,),
        )
        snap = await cur.fetchone()
        old_schema = None
        if snap:
            old_schema = await anyio.to_thread.run_sync(
                apps.read_snapshot_schema, data_dir, snap["source_snapshot"]
            )
        cur = await conn.execute(
            "SELECT version, commit FROM app_versions WHERE app_id = %s AND kind = 'published' "
            "ORDER BY published_at DESC, id DESC",
            (app_id,),
        )
        published = await cur.fetchall()
    build_id, doc, found = await anyio.to_thread.run_sync(_stage, r, slug, builds)
    if build_id is None:
        return app, None, found, []
    start = time.monotonic()
    schema_sql = tree = bump = None
    try:
        if not found and doc is not None and app.get("working_version"):
            new_schema = await anyio.to_thread.run_sync(_staged_schema, builds, build_id)
            found = [
                diagnostic("manifest", d.file, d.message, d.path)
                for d in manifest.export_contract_diagnostics(
                    app["working_version"]["manifest"], old_schema, doc, new_schema
                )
            ]
        if found:
            return app, None, found, []
        reads_sql = await _reads_stand_ins(pool, principal, history, app_id, doc)
        if reads_sql:
            await anyio.to_thread.run_sync(_write_reads, builds, build_id, reads_sql)
        found, output = await _run(builds, builder, build_id)
        if output is not None:
            schema_sql = await anyio.to_thread.run_sync(_staged_schema, builds, build_id)
            tree = await anyio.to_thread.run_sync(_staged_tree, history, app_id, builds, build_id)
            version = await anyio.to_thread.run_sync(
                _bumped_version, history, app_id, doc["version"], published, tree
            )
            if version is not None:
                bump = await anyio.to_thread.run_sync(
                    _set_staged_version, builds, build_id, version
                )
                doc["version"] = version
                tree = await anyio.to_thread.run_sync(
                    _staged_tree, history, app_id, builds, build_id
                )
    finally:
        await anyio.to_thread.run_sync(builds.discard, build_id)
    duration_ms = int((time.monotonic() - start) * 1000)
    if output is None:
        return app, Build(build_id, False, duration_ms), found, []

    thread_id = _thread_id(principal)
    subject = f"Build {doc['version']}" + (" by the agent" if thread_id else "")
    text = apphistory.message(
        subject, version=doc["version"], user=principal.username, thread_id=thread_id
    )
    commit = (history, tree, text) if history is not None and tree is not None else None
    bundle_path = await anyio.to_thread.run_sync(_store_bundle, data_dir, app_id, build_id, output)
    try:
        async with pool.connection() as conn:
            previous, commit_id = await _record(conn, principal, app_id, doc, bundle_path, commit)
    except BaseException:
        await anyio.to_thread.run_sync(_drop_bundle, data_dir, bundle_path)
        raise
    await anyio.to_thread.run_sync(_drop_bundle, data_dir, previous)
    if bump is not None:
        written = principal.uid is not None and await anyio.to_thread.run_sync(
            _write_back_version, r, *bump, fsops.Owner(principal.uid, r.space["gid"])
        )
        if not written:
            logger.warning("builds: app %s: built %s; app.json not updated", app_id, doc["version"])
    async with pool.connection() as conn:
        app = await apps.get_visible_app(conn, principal, app_id)
    migrations = await appdata.built(principal, app, doc["version"], schema_sql)
    build = Build(build_id, True, duration_ms, bundle_path, len(output[0]), commit_id)
    return app, build, [], migrations


async def _lock_working(conn, principal: Principal, app: dict[str, Any]) -> dict[str, Any]:
    """The working version's row, locked for the transaction; then `write` on the source space.

    The lock serializes builds and reverts of one app, commits to its history included.
    """
    cur = await conn.execute(
        "SELECT id, bundle_path FROM app_versions "
        "WHERE app_id = %s AND kind = 'working' FOR UPDATE",
        (app["id"],),
    )
    row = await cur.fetchone()
    if row is None:
        raise NotFound("not_found")
    # After the row lock: a space archived meanwhile has released its bundles.
    await spaces.authorize_space(conn, principal, app["source_space_id"], "write")
    return row


async def _commit(app_id: UUID, commit: tuple[AppHistory, str, str] | None) -> str | None:
    if commit is None:
        return None
    history, tree, text = commit
    try:
        commit_id, _new = await anyio.to_thread.run_sync(history.commit, app_id, tree, text)
    except HistoryError as exc:
        logger.error("history: app %s: commit failed: %s", app_id, exc)
        return None
    return commit_id


async def _record(
    conn,
    principal: Principal,
    app_id: UUID,
    doc: Any,
    bundle_path: str,
    commit: tuple[AppHistory, str, str] | None,
) -> tuple[str | None, str | None]:
    """Make `bundle_path` (and the commit of `(history, tree, message)`) the working version's.

    (the bundle it replaced if nothing else uses it, the commit id).
    """
    app = await apps.get_visible_app(conn, principal, app_id)
    async with conn.transaction():
        row = await _lock_working(conn, principal, app)
        commit_id = await _commit(app_id, commit)
        await conn.execute("UPDATE apps SET name = %s WHERE id = %s", (doc["name"], app_id))
        await conn.execute(
            "UPDATE app_versions SET version = %s, manifest = %s, bundle_path = %s, commit = %s "
            "WHERE id = %s",
            (doc["version"], Jsonb(doc), bundle_path, commit_id, row["id"]),
        )
        await conn.execute(
            "UPDATE app_instances SET granted_reads = %s "
            "WHERE app_id = %s AND tracks = 'working' AND uninstalled_at IS NULL",
            (Jsonb(manifest.homeai_reads(doc)), app_id),
        )
        previous = row["bundle_path"]
        if previous and previous != bundle_path:
            cur = await conn.execute(
                "SELECT 1 FROM app_versions WHERE bundle_path = %s", (previous,)
            )
            if await cur.fetchone() is None:
                return previous, commit_id
    return None, commit_id


# --- reverting ------------------------------------------------------------------------


def _as_tree(files: dict[str, bytes]) -> dict[str, Any]:
    """{name: bytes | {name: ...}} of a commit's `{path: content}`."""
    tree: dict[str, Any] = {}
    for path, data in files.items():
        *dirs, name = path.split("/")
        node = tree
        for part in [*dirs, name]:
            if part in ("", ".", "..") or part.startswith(".") or "\x00" in part:
                raise HistoryError("a path in the commit isn't a staged path")
        for part in dirs:
            node = node.setdefault(part, {})
            if not isinstance(node, dict):
                raise HistoryError("a path in the commit is both a file and a folder")
        if name in node:
            raise HistoryError("a path in the commit is both a file and a folder")
        node[name] = data
    return tree


def _prune(dir_fd: int, name: str, owner: fsops.Owner) -> None:
    """Remove entry `name`, except for the dotfiles in it: a folder holding some stays."""
    try:
        fd = os.open(name, _OPEN_DIR, dir_fd=dir_fd)
    except OSError as exc:
        if exc.errno not in (errno.ELOOP, errno.ENOTDIR):
            raise
        fsops.remove_at(dir_fd, name)
        return
    try:
        _sync(fd, {}, owner)
    finally:
        os.close(fd)
    try:
        os.rmdir(name, dir_fd=dir_fd)
    except OSError as exc:
        if exc.errno != errno.ENOTEMPTY:
            raise


def _sync(dir_fd: int, tree: dict[str, Any], owner: fsops.Owner) -> None:
    """Make the folder open as `dir_fd` hold exactly `tree`, dotfiles aside, by fd only."""
    with os.scandir(dir_fd) as it:
        present = [entry.name for entry in it]
    for name in present:
        if not name.startswith(".") and name not in tree:
            _prune(dir_fd, name, owner)
    for name, want in sorted(tree.items()):
        if isinstance(want, dict):
            fd = fsops.open_dir_at(dir_fd, name, owner)
            try:
                _sync(fd, want, owner)
            finally:
                os.close(fd)
        else:
            fsops.replace_file_at(dir_fd, name, want, owner)


def _write_source(r: vfs.Resolved, files: dict[str, bytes], owner: fsops.Owner) -> None:
    tree = _as_tree(files)
    fd = apps.open_source(r)
    if fd is None:
        with r.open_root() as root:
            fsops.make_dirs(root, r.rel, owner)
        fd = apps.open_source(r)
        if fd is None:
            raise NotFound("not_found")
    try:
        _sync(fd, tree, owner)
    finally:
        os.close(fd)


async def revert_app(
    pool: AsyncConnectionPool,
    principal: Principal,
    storage: SpaceStorage,
    builds: Builds,
    builder: Builder,
    data_dir: Path,
    appdata: AppData,
    history: AppHistory,
    app_id: UUID,
    rev: str,
) -> tuple[str, dict[str, Any], Build | None, list[Diagnostic], list[dict[str, Any]]]:
    """(the history's head after the revert, then what `build_app` returns for the rebuild).

    Needs `write` on the source space. `rev` must be a commit on the app's
    branch (else 422 `unknown_commit`). A new commit gets `rev`'s tree (none
    if the head has it already); that tree is written back into the source
    folder by fd (`fsops`), replacing what's there except dotfiles, never
    following a link; then the app is rebuilt, which commits nothing more
    unless the folder changed meanwhile.
    """
    async with pool.connection() as conn:
        app = await apps.get_visible_app(conn, principal, app_id)
        await spaces.authorize_space(conn, principal, app["source_space_id"], "write")
        r, _slug = await apps.resolve_source(conn, principal, storage, app["source_path"])
    try:
        target = await anyio.to_thread.run_sync(history.resolve, app_id, rev)
        if target is None:
            raise InvalidInput("unknown_commit")
        files = await anyio.to_thread.run_sync(
            history.read_tree, app_id, target, manifest.MAX_PACKAGE_ENTRIES, MAX_SOURCE_BYTES
        )
        original = await anyio.to_thread.run_sync(history.get, app_id, target)
        text = apphistory.message(
            f"Revert to {target[:12]}" + (f" ({original.version})" if original.version else ""),
            version=original.version or "", user=principal.username,
            thread_id=_thread_id(principal), reverts=target,
        )  # fmt: skip
        async with pool.connection() as conn, conn.transaction():
            await _lock_working(conn, principal, app)
            head, _new = await anyio.to_thread.run_sync(history.commit_revert, app_id, target, text)
    except HistoryError as exc:
        logger.error("history: app %s: revert to %s failed: %s", app_id, rev, exc)
        raise ServerError("history_failed") from exc
    owner = fsops.Owner(principal.uid, r.space["gid"])
    await anyio.to_thread.run_sync(_write_source, r, files, owner)
    result = await build_app(
        pool, principal, storage, builds, builder, data_dir, appdata, history, app_id
    )
    return head, *result


# --- serving -------------------------------------------------------------------------

_INSTANCE_BUNDLE = """
SELECT i.space_id, i.app_id, v.version, v.manifest -> 'homeai' ->> 'sdk' AS sdk, v.bundle_path
FROM app_instances i
JOIN app_versions v ON v.id = i.version_id
    OR (i.version_id IS NULL AND v.app_id = i.app_id AND v.kind = 'working')
WHERE i.id = %s AND i.uninstalled_at IS NULL
"""


def _read_bundle(data_dir: Path, bundle_path: str) -> bytes | None:
    if not BUNDLE_PATH_RE.fullmatch(bundle_path):
        return None
    try:
        dir_fd = os.open(data_dir, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
        try:
            with fsops.open_regular_at(dir_fd, bundle_path) as f:
                data = f.read(MAX_BUNDLE_BYTES + 1)
        finally:
            os.close(dir_fd)
    except OSError:
        return None
    if len(data) > MAX_BUNDLE_BYTES or not data.startswith(BUNDLE_PREFIX):
        return None
    return data


async def instance_bundle(
    pool: AsyncConnectionPool, principal: Principal, data_dir: Path, instance_id: UUID
) -> dict:
    """The bundle of the version `instance_id` tracks, for its space's readers."""
    # A rebuild removes the old bundle right after pointing the row at the new one.
    for _ in range(2):
        async with pool.connection() as conn:
            cur = await conn.execute(_INSTANCE_BUNDLE, (instance_id,))
            row = await cur.fetchone()
            if row is None:
                raise NotFound("not_found")
            await spaces.authorize_space(conn, principal, row["space_id"], "read")
        if row["bundle_path"] is None:
            raise NotFound("no_bundle")
        data = await anyio.to_thread.run_sync(_read_bundle, data_dir, row["bundle_path"])
        if data is not None:
            return {
                "app_id": row["app_id"],
                "version": row["version"],
                "sdk": row["sdk"],
                "bundle_id": row["bundle_path"].split("/")[2],
                "code": data.decode("utf-8", errors="replace"),
            }
    logger.error("instance %s: bundle %s is missing or unusable", instance_id, row["bundle_path"])
    raise NotFound("no_bundle")

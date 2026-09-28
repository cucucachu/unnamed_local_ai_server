"""App data: RPC, computed migrations, and change events per instance (docs/PLATFORM.md §7).

Access is the instance's space's (`spaces.authorize_space`): `read` for
`getAll`/`getFirst` and the migration history, `write` for `run`,
`transaction`, `action` and migrations; a non-member gets `404` exactly as
for an unknown instance. Agent delegations have their user's rights (D6).

Actions (`actions/<name>.sql`) and `schema.sql` are read from the app's
source at call time (the working copy, D15) or, for a pinned published
version, from that version's package snapshot (`app-releases/…`).

Writes and migrations of one instance are serialized by an in-process lock
(the platform runs one worker). A write that changed rows, or an applied
migration, emits `db_changed` and schedules a refresh of `ro/data.sqlite`
at most once per `PUBLISH_INTERVAL_S` (trailing, so the last write is
always published).

Migrations (`app_migrations`): a plan with only additive/safe steps is
applied at once; one with a destructive step is stored `pending` and
applied only by `approve`, which re-plans and refuses (`409 plan_changed`)
if the live database or the stored schema no longer give the same steps.
Every apply is preceded by a snapshot when the database has any table, and
rolls back as a whole on failure.
"""

from __future__ import annotations

import asyncio
import errno
import logging
import os
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TypeVar
from uuid import UUID, uuid4

import anyio.to_thread
from psycopg import AsyncConnection
from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool

from app.core import appdb, apps, appschema, beneath, fsops, spaces, vfs
from app.core.errors import (
    Conflict,
    InvalidApp,
    InvalidInput,
    MigrationFailed,
    NotFound,
    PlatformError,
    ServerError,
    SqlFailed,
)
from app.core.events import EventHub
from app.core.principal import Principal
from app.core.storage import SpaceStorage, StorageError

logger = logging.getLogger(__name__)

T = TypeVar("T")
Row = dict[str, Any]

READ_OPS = frozenset({"getAll", "getFirst"})
ACTION_NAME_RE = re.compile(r"^[a-z][a-zA-Z0-9_]*$")
MAX_ACTION_BYTES = 64 * 1024
PUBLISH_INTERVAL_S = 1.0

_OPEN_DIR = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC

_INSTANCE = """
SELECT i.id, i.app_id, i.space_id, i.tracks, i.version_id, a.slug, a.source_path,
       v.source_snapshot
FROM app_instances i
JOIN apps a ON a.id = i.app_id
LEFT JOIN app_versions v ON v.id = i.version_id
WHERE i.id = %s AND i.uninstalled_at IS NULL
"""

_MIGRATION_COLUMNS = (
    "id, instance_id, status, steps, summary, needs_approval, snapshot, error, "
    "created_by, created_at, decided_by, decided_at"
)


@dataclass(frozen=True)
class Target:
    instance: Row
    space: Row

    @property
    def id(self) -> UUID:
        return self.instance["id"]

    @property
    def space_id(self) -> UUID:
        return self.space["id"]

    @property
    def gid(self) -> int:
        return self.space["gid"]


def _schema_diagnostic(message: str) -> InvalidApp:
    return InvalidApp([{"file": "schema.sql", "path": "", "message": message}], "invalid_schema")


def _up_to_date(instance_id: UUID) -> Row:
    return {
        "id": None, "instance_id": instance_id, "status": "up_to_date", "steps": [],
        "summary": dict.fromkeys(appschema.KINDS, 0), "needs_approval": False, "snapshot": None,
        "error": None, "created_by": None, "created_at": None, "decided_by": None,
        "decided_at": None,
    }  # fmt: skip


def _read_package_fd(dir_fd: int, parts: tuple[str, ...], limit: int) -> bytes | None:
    """`parts` below an already-open package directory, or None if missing/not a file."""
    current = dir_fd
    opened: list[int] = []
    try:
        for name in parts[:-1]:
            sub = os.open(name, _OPEN_DIR, dir_fd=current)
            opened.append(sub)
            current = sub
        with fsops.open_regular_at(current, parts[-1]) as f:
            data = f.read(limit + 1)
    except OSError as exc:
        if exc.errno in (errno.ENOENT, errno.ENOTDIR, errno.ELOOP, errno.EISDIR):
            return None
        raise
    finally:
        for fd in reversed(opened):
            os.close(fd)
    if len(data) > limit:
        raise InvalidInput("file_too_large")
    return data


def _read_package_file(r: vfs.Resolved, parts: tuple[str, ...], limit: int) -> bytes | None:
    """`parts` below the package folder, or None if it (or the folder) isn't a regular file."""
    with r.open_root() as root:
        try:
            dir_fd = beneath.open(root, r.rel, os.O_RDONLY | os.O_DIRECTORY, symlinks=False)
        except OSError as exc:
            if exc.errno in (errno.ENOENT, errno.ENOTDIR, errno.ELOOP):
                return None
            raise
        try:
            return _read_package_fd(dir_fd, parts, limit)
        finally:
            os.close(dir_fd)


class AppData:
    def __init__(
        self,
        pool: AsyncConnectionPool,
        storage: SpaceStorage,
        hub: EventHub,
        data_dir: Path | None = None,
    ) -> None:
        self.pool = pool
        self.storage = storage
        self.hub = hub
        self.data_dir = data_dir
        self._locks: dict[UUID, asyncio.Lock] = {}
        self._publishing: dict[UUID, asyncio.Task] = {}
        self._published_at: dict[UUID, float] = {}

    # --- plumbing ---------------------------------------------------------------------

    def _lock(self, instance_id: UUID) -> asyncio.Lock:
        return self._locks.setdefault(instance_id, asyncio.Lock())

    async def _target(
        self, conn: AsyncConnection, principal: Principal, instance_id: UUID, need: spaces.Need
    ) -> Target:
        cur = await conn.execute(_INSTANCE, (instance_id,))
        instance = await cur.fetchone()
        if instance is None:
            raise NotFound("not_found")
        access = await spaces.authorize_space(conn, principal, instance["space_id"], need)
        return Target(instance, access.space)

    def _in_instance(self, target: Target, fn: Callable[[Any, int], T]) -> T:
        try:
            fd = self.storage.open_instance(target.space_id, target.gid, target.id, create=False)
        except FileNotFoundError:
            raise NotFound("not_found") from None
        try:
            with appdb.connect(fd) as con:
                return fn(con, fd)
        finally:
            os.close(fd)

    async def _run(self, target: Target, fn: Callable[[Any, int], T]) -> T:
        try:
            return await anyio.to_thread.run_sync(self._in_instance, target, fn)
        except appdb.SqlError as exc:
            raise SqlFailed(exc.code, exc.message, exc.index) from exc
        except appschema.SchemaError as exc:
            raise _schema_diagnostic(str(exc)) from exc
        except (appdb.InstanceStorageError, StorageError) as exc:
            logger.error("instance %s: %s", target.id, exc)
            raise ServerError("instance_storage_invalid") from exc

    async def _read_from_package(
        self, conn: AsyncConnection, principal: Principal, target: Target, parts: tuple[str, ...]
    ) -> bytes | None:
        limit = appschema.MAX_SCHEMA_BYTES if parts[-1] == "schema.sql" else MAX_ACTION_BYTES
        if target.instance["tracks"] == "working":
            r = await vfs.resolve_virtual_path(
                conn, principal, self.storage, target.instance["source_path"], "read"
            )
            return await anyio.to_thread.run_sync(_read_package_file, r, parts, limit)
        rel = target.instance.get("source_snapshot")
        if not rel or self.data_dir is None:
            raise Conflict("pinned_versions_unsupported")

        def _read() -> bytes | None:
            fd = apps.open_snapshot(self.data_dir, rel)
            try:
                return _read_package_fd(fd, parts, limit)
            finally:
                os.close(fd)

        try:
            return await anyio.to_thread.run_sync(_read)
        except OSError as exc:
            raise ServerError("release_invalid") from exc

    async def _read_schema(
        self, conn: AsyncConnection, principal: Principal, target: Target
    ) -> str:
        try:
            data = await self._read_from_package(conn, principal, target, ("schema.sql",))
        except InvalidInput as exc:
            raise _schema_diagnostic("schema.sql is larger than 256 KiB") from exc
        if data is None:
            raise _schema_diagnostic("schema.sql is missing")
        try:
            return data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise _schema_diagnostic("schema.sql is not valid UTF-8") from exc

    async def _read_action(
        self, conn: AsyncConnection, principal: Principal, target: Target, name: str
    ) -> str:
        if not ACTION_NAME_RE.fullmatch(name):
            raise InvalidInput("invalid_action")
        data = await self._read_from_package(conn, principal, target, ("actions", f"{name}.sql"))
        if data is None:
            raise NotFound("unknown_action")
        try:
            return data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise InvalidInput("invalid_action") from exc

    def _changed(self, target: Target) -> None:
        self.hub.db_changed(target.space_id, target.id)
        if target.id in self._publishing:
            return
        last = self._published_at.get(target.id)
        delay = 0.0 if last is None else max(0.0, last + PUBLISH_INTERVAL_S - time.monotonic())
        self._publishing[target.id] = asyncio.create_task(self._publish_later(target, delay))

    async def _publish_later(self, target: Target, delay: float) -> None:
        try:
            await asyncio.sleep(delay)
        finally:
            self._publishing.pop(target.id, None)
        self._published_at[target.id] = time.monotonic()
        try:
            await anyio.to_thread.run_sync(lambda: self._in_instance(target, appdb.publish))
        except NotFound:
            pass  # uninstalled meanwhile
        except Exception:
            logger.exception("instance %s: publishing ro/%s failed", target.id, appdb.DB_NAME)

    async def flush(self) -> None:
        """Wait for every scheduled `ro/` publish."""
        while self._publishing:
            await asyncio.gather(*self._publishing.values(), return_exceptions=True)

    async def aclose(self) -> None:
        for task in self._publishing.values():
            task.cancel()
        await asyncio.gather(*self._publishing.values(), return_exceptions=True)

    # --- RPC --------------------------------------------------------------------------

    async def rpc(self, principal: Principal, instance_id: UUID, req: dict) -> dict:
        op = req["op"]
        async with self.pool.connection() as conn:
            target = await self._target(
                conn, principal, instance_id, "read" if op in READ_OPS else "write"
            )
            action_sql = (
                await self._read_action(conn, principal, target, req["name"])
                if op == "action"
                else None
            )
        if op == "getAll":
            rows = await self._run(target, lambda c, _: appdb.get_all(c, req["sql"], req["params"]))
            return {"rows": rows}
        if op == "getFirst":
            row = await self._run(
                target, lambda c, _: appdb.get_first(c, req["sql"], req["params"])
            )
            return {"row": row}

        if op == "run":
            fn = lambda c, _: appdb.run(c, req["sql"], req["params"])
        elif op == "transaction":
            statements = [(s["sql"], s["params"]) for s in req["statements"]]
            fn = lambda c, _: {"results": appdb.transaction(c, statements)}
        else:
            fn = lambda c, _: appdb.action(c, action_sql, req["params"])
        async with self._lock(target.id):
            result = await self._run(target, fn)
        changes = (
            sum(r["changes"] for r in result["results"])
            if op == "transaction"
            else result["changes"]
        )
        if changes:
            self._changed(target)
        return result

    # --- migrations ---------------------------------------------------------------------

    async def _get_migration(
        self, conn: AsyncConnection, instance_id: UUID, migration_id: UUID
    ) -> Row:
        cur = await conn.execute(
            f"SELECT {_MIGRATION_COLUMNS}, schema_sql FROM app_migrations "
            "WHERE id = %s AND instance_id = %s",
            (migration_id, instance_id),
        )
        row = await cur.fetchone()
        if row is None:
            raise NotFound("not_found")
        return row

    async def list_migrations(self, principal: Principal, instance_id: UUID) -> list[Row]:
        async with self.pool.connection() as conn:
            await self._target(conn, principal, instance_id, "read")
            cur = await conn.execute(
                f"SELECT {_MIGRATION_COLUMNS} FROM app_migrations WHERE instance_id = %s "
                "ORDER BY created_at DESC, id LIMIT 50",
                (instance_id,),
            )
            return await cur.fetchall()

    async def migrate(self, principal: Principal, instance_id: UUID) -> Row:
        """Plan `schema.sql` against the live database; apply it unless it's destructive."""
        async with self.pool.connection() as conn:
            target = await self._target(conn, principal, instance_id, "write")
            schema_sql = await self._read_schema(conn, principal, target)
        return await self._migrate(target, principal, schema_sql)

    async def _migrate(self, target: Target, principal: Principal, schema_sql: str) -> Row:
        async with self._lock(target.id):
            plan = await self._run(target, lambda c, _: appschema.plan(c, schema_sql))
            if not plan.steps:
                return _up_to_date(target.id)
            if not plan.needs_approval:
                return await self._apply(target, principal, uuid4(), plan, schema_sql)
            async with self.pool.connection() as conn, conn.transaction():
                await conn.execute(
                    "UPDATE app_migrations SET status = 'superseded', decided_at = now() "
                    "WHERE instance_id = %s AND status = 'pending'",
                    (target.id,),
                )
                cur = await conn.execute(
                    "INSERT INTO app_migrations "
                    "(instance_id, status, schema_sql, steps, summary, needs_approval, created_by) "
                    f"VALUES (%s, 'pending', %s, %s, %s, true, %s) RETURNING {_MIGRATION_COLUMNS}",
                    (target.id, schema_sql, Jsonb(plan.as_dicts()), Jsonb(plan.summary),
                     principal.user_id),
                )  # fmt: skip
                return await cur.fetchone()

    async def built(
        self, principal: Principal, app: Row, version: str, schema_sql: str | None
    ) -> list[dict]:
        """After a build of `app`'s working version: migrate every live instance tracking it
        to the built `schema_sql` (None if it couldn't be read), then emit `app_built`.

        One `{"instance_id", "migration", "error"}` per instance; a failure there
        doesn't fail the build.
        """
        async with self.pool.connection() as conn:
            cur = await conn.execute(
                "SELECT id FROM app_instances "
                "WHERE app_id = %s AND tracks = 'working' AND uninstalled_at IS NULL "
                "ORDER BY created_at, id",
                (app["id"],),
            )
            ids = [row["id"] for row in await cur.fetchall()]
        results = []
        for instance_id in ids:
            result: dict[str, Any] = {"instance_id": instance_id, "migration": None, "error": None}
            try:
                if schema_sql is None:
                    raise _schema_diagnostic("schema.sql can't be used for a migration")
                async with self.pool.connection() as conn:
                    target = await self._target(conn, principal, instance_id, "write")
                result["migration"] = await self._migrate(target, principal, schema_sql)
            except MigrationFailed as exc:
                result["migration"], result["error"] = exc.migration, exc.code
            except PlatformError as exc:
                result["error"] = exc.code
            results.append(result)
        self.hub.app_built([app["source_space_id"]], app["id"], version)
        return results

    async def uninstall(self, principal: Principal, space_id: UUID, instance_id: UUID) -> None:
        """`apps.uninstall_app` once no write or migration of the instance is running."""
        async with self._lock(instance_id), self.pool.connection() as conn:
            await apps.uninstall_app(conn, principal, self.storage, space_id, instance_id)

    async def pending(self, principal: Principal, instance_id: UUID, migration_id: UUID) -> Row:
        """The migration, if `principal` could approve it now (write access, still pending)."""
        async with self.pool.connection() as conn:
            target = await self._target(conn, principal, instance_id, "write")
            row = await self._get_migration(conn, target.id, migration_id)
        if row["status"] != "pending":
            raise Conflict("migration_not_pending")
        return row

    async def approve(self, principal: Principal, instance_id: UUID, migration_id: UUID) -> Row:
        async with self.pool.connection() as conn:
            target = await self._target(conn, principal, instance_id, "write")
        async with self._lock(target.id):
            async with self.pool.connection() as conn:
                row = await self._get_migration(conn, target.id, migration_id)
            if row["status"] != "pending":
                raise Conflict("migration_not_pending")
            schema_sql = row["schema_sql"]
            plan = await self._run(target, lambda c, _: appschema.plan(c, schema_sql))
            if plan.as_dicts() != row["steps"]:
                await self._decide(migration_id, principal, "superseded")
                raise Conflict("plan_changed")
            return await self._apply(target, principal, migration_id, plan, schema_sql, row)

    async def reject(self, principal: Principal, instance_id: UUID, migration_id: UUID) -> Row:
        async with self.pool.connection() as conn:
            target = await self._target(conn, principal, instance_id, "write")
        async with self._lock(target.id):
            async with self.pool.connection() as conn:
                row = await self._get_migration(conn, target.id, migration_id)
            if row["status"] != "pending":
                raise Conflict("migration_not_pending")
            return await self._decide(migration_id, principal, "rejected")

    async def _decide(
        self, migration_id: UUID, principal: Principal, status: str, **fields: Any
    ) -> Row:
        sets = "".join(f", {k} = %({k})s" for k in fields)
        async with self.pool.connection() as conn:
            cur = await conn.execute(
                f"UPDATE app_migrations SET status = %(status)s, decided_by = %(by)s, "
                f"decided_at = now(){sets} WHERE id = %(id)s RETURNING {_MIGRATION_COLUMNS}",
                {"status": status, "by": principal.user_id, "id": migration_id, **fields},
            )
            return await cur.fetchone()

    async def _apply(
        self,
        target: Target,
        principal: Principal,
        migration_id: UUID,
        plan: appschema.Plan,
        schema_sql: str,
        pending: Row | None = None,
    ) -> Row:
        tag = str(migration_id)
        snapshot, error = await self._run(
            target, lambda c, fd: appdb.migrate(c, fd, plan.steps, schema_sql, tag)
        )
        status = "failed" if error else "applied"
        if pending is not None:
            row = await self._decide(
                migration_id, principal, status, snapshot=snapshot, error=error
            )
        else:
            async with self.pool.connection() as conn:
                cur = await conn.execute(
                    "INSERT INTO app_migrations (id, instance_id, status, schema_sql, steps, "
                    "summary, needs_approval, snapshot, error, created_by, decided_by, decided_at) "
                    "VALUES (%s, %s, %s, %s, %s, %s, false, %s, %s, %s, %s, now()) "
                    f"RETURNING {_MIGRATION_COLUMNS}",
                    (migration_id, target.id, status, schema_sql, Jsonb(plan.as_dicts()),
                     Jsonb(plan.summary), snapshot, error, principal.user_id, principal.user_id),
                )  # fmt: skip
                row = await cur.fetchone()
        if error:
            raise MigrationFailed(row)
        self._changed(target)
        return row

"""Forward-only SQL migration runner.

Migrations are `app/db/migrations/NNNN_name.sql` files, applied in version
order. Each applied file is recorded in `schema_migrations` with a SHA-256 of
its contents; editing an already-applied file is an error rather than a
silent no-op, so add a new migration instead.

All pending migrations are applied in ONE transaction that first takes a
transaction-scoped advisory lock. Concurrent starters therefore serialize:
the second one waits, then sees everything the first one recorded and
applies nothing. A failing migration rolls back the whole batch.
"""

from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass
from pathlib import Path

from psycopg import AsyncConnection
from psycopg.rows import tuple_row

logger = logging.getLogger(__name__)

MIGRATIONS_DIR = Path(__file__).parent / "migrations"

# Arbitrary but fixed bigint ("homeai" + 0x0001) identifying this lock.
ADVISORY_LOCK_KEY = 0x686F6D6561690001

_FILENAME_RE = re.compile(r"^(?P<version>\d{4})_(?P<name>[a-z0-9_]+)\.sql$")


class MigrationError(Exception):
    pass


@dataclass(frozen=True)
class Migration:
    version: int
    name: str
    sql: str

    @property
    def checksum(self) -> str:
        return hashlib.sha256(self.sql.encode()).hexdigest()

    @property
    def filename(self) -> str:
        return f"{self.version:04d}_{self.name}.sql"


def load_migrations(directory: Path = MIGRATIONS_DIR) -> list[Migration]:
    migrations: dict[int, Migration] = {}
    for path in directory.glob("*.sql"):
        match = _FILENAME_RE.match(path.name)
        if match is None:
            raise MigrationError(f"migration filename must match NNNN_name.sql: {path.name}")
        version = int(match["version"])
        if version in migrations:
            raise MigrationError(
                f"duplicate migration version {version:04d}: "
                f"{migrations[version].filename} and {path.name}"
            )
        migrations[version] = Migration(version, match["name"], path.read_text())
    return [migrations[v] for v in sorted(migrations)]


async def _applied_checksums(conn: AsyncConnection) -> dict[int, str]:
    async with conn.cursor(row_factory=tuple_row) as cur:
        await cur.execute("SELECT to_regclass('schema_migrations') IS NOT NULL")
        (present,) = await cur.fetchone()
        if not present:
            return {}
        await cur.execute("SELECT version, checksum FROM schema_migrations")
        return dict(await cur.fetchall())


async def run_migrations(
    conn: AsyncConnection, directory: Path = MIGRATIONS_DIR
) -> list[Migration]:
    """Apply every pending migration in `directory`; return the ones applied."""
    migrations = load_migrations(directory)

    async with conn.transaction():
        await conn.execute("SELECT pg_advisory_xact_lock(%s)", (ADVISORY_LOCK_KEY,))
        applied = await _applied_checksums(conn)

        for migration in migrations:
            recorded = applied.get(migration.version)
            if recorded is not None and recorded != migration.checksum:
                raise MigrationError(
                    f"{migration.filename} was modified after being applied "
                    "(checksum mismatch); add a new migration instead"
                )
        known = {m.version for m in migrations}
        for version in sorted(applied.keys() - known):
            logger.warning("schema_migrations has version %04d with no matching file", version)

        pending = [m for m in migrations if m.version not in applied]
        for migration in pending:
            try:
                await conn.execute(migration.sql)
            except Exception as exc:
                raise MigrationError(f"{migration.filename} failed: {exc}") from exc
            await conn.execute(
                "INSERT INTO schema_migrations (version, name, checksum) VALUES (%s, %s, %s)",
                (migration.version, migration.name, migration.checksum),
            )
            logger.info("applied migration %s", migration.filename)

    return pending

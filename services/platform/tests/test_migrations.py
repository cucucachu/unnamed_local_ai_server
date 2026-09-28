import asyncio

import psycopg
import pytest
from psycopg.rows import dict_row

from app.db.migrate import MIGRATIONS_DIR, MigrationError, load_migrations, run_migrations

SHIPPED = [
    (1, "init"),
    (2, "accounts"),
    (3, "spaces"),
    (4, "apps"),
    (5, "app_data"),
    (6, "app_sharing"),
    (7, "app_exports"),
    (8, "wireguard"),
    (9, "webauthn"),
]


async def _connect(dsn: str) -> psycopg.AsyncConnection:
    return await psycopg.AsyncConnection.connect(dsn, autocommit=True, row_factory=dict_row)


async def _tables(conn) -> set[str]:
    cur = await conn.execute("SELECT tablename FROM pg_tables WHERE schemaname = current_schema()")
    return {row["tablename"] for row in await cur.fetchall()}


async def _recorded(conn) -> list[tuple[int, str]]:
    cur = await conn.execute("SELECT version, name FROM schema_migrations ORDER BY version")
    return [(row["version"], row["name"]) for row in await cur.fetchall()]


def _write(directory, files: dict[str, str]):
    directory.mkdir(exist_ok=True)
    for name, body in files.items():
        (directory / name).write_text(body)
    return directory


def _with_init(tmp_path, **extra: str):
    """A migrations dir holding the real 0001 plus `extra` files."""
    files = {"0001_init.sql": (MIGRATIONS_DIR / "0001_init.sql").read_text(), **extra}
    return _write(tmp_path / "migrations", files)


# --- against the shipped migrations ----------------------------------------


async def test_fresh_database_gets_initial_schema(pg_database):
    async with await _connect(pg_database.dsn) as conn:
        applied = await run_migrations(conn)
        assert [m.filename for m in applied] == [
            "0001_init.sql",
            "0002_accounts.sql",
            "0003_spaces.sql",
            "0004_apps.sql",
            "0005_app_data.sql",
            "0006_app_sharing.sql",
            "0007_app_exports.sql",
            "0008_wireguard.sql",
            "0009_webauthn.sql",
        ]
        tables = {"schema_migrations", "platform_state", "users", "sessions", "invites"}
        tables |= {"spaces", "space_members", "apps", "app_versions", "app_instances"}
        tables |= {"app_migrations", "app_catalog"}
        tables |= {"wireguard_peers", "webauthn_credentials", "webauthn_challenges"}
        assert tables <= (await _tables(conn))
        assert await _recorded(conn) == SHIPPED


async def test_second_run_applies_nothing(pg_database):
    async with await _connect(pg_database.dsn) as conn:
        assert await run_migrations(conn)
        assert await run_migrations(conn) == []
        assert await _recorded(conn) == SHIPPED


async def test_platform_state_is_key_value_jsonb(pg_database):
    async with await _connect(pg_database.dsn) as conn:
        await run_migrations(conn)
        await conn.execute(
            "INSERT INTO platform_state (key, value) VALUES (%s, %s::jsonb)",
            ("bootstrap_admin_id", '"00000000-0000-0000-0000-000000000001"'),
        )
        cur = await conn.execute(
            "SELECT value FROM platform_state WHERE key = 'bootstrap_admin_id'"
        )
        assert (await cur.fetchone())["value"] == "00000000-0000-0000-0000-000000000001"


async def test_concurrent_runners_apply_once(pg_database):
    """The advisory lock serializes starters; the loser sees the winner's work."""
    conns = [await _connect(pg_database.dsn) for _ in range(4)]
    try:
        results = await asyncio.gather(*(run_migrations(c) for c in conns))
        assert sorted(len(r) for r in results) == [0, 0, 0, len(SHIPPED)]
        assert await _recorded(conns[0]) == SHIPPED
    finally:
        for c in conns:
            await c.close()


# --- runner behaviour with synthetic migration sets ------------------------


async def test_later_migrations_apply_incrementally_in_order(pg_database, tmp_path):
    directory = _with_init(tmp_path)
    async with await _connect(pg_database.dsn) as conn:
        await run_migrations(conn, directory)
        _write(
            directory,
            {
                "0003_c.sql": "ALTER TABLE b ADD COLUMN note TEXT;",
                "0002_b.sql": "CREATE TABLE b (id INT); INSERT INTO b VALUES (1);",
            },
        )
        applied = await run_migrations(conn, directory)
        assert [m.version for m in applied] == [2, 3]
        assert await _recorded(conn) == [(1, "init"), (2, "b"), (3, "c")]
        assert await run_migrations(conn, directory) == []


async def test_failing_migration_rolls_back_whole_batch(pg_database, tmp_path):
    directory = _with_init(
        tmp_path,
        **{
            "0002_ok.sql": "CREATE TABLE should_not_survive (id INT);",
            "0003_broken.sql": "CREATE TABLE nope (id INT); SELECT * FROM missing_table;",
        },
    )
    async with await _connect(pg_database.dsn) as conn:
        with pytest.raises(MigrationError, match="0003_broken.sql failed"):
            await run_migrations(conn, directory)
        assert await _tables(conn) == set()

        (directory / "0003_broken.sql").write_text("CREATE TABLE fixed (id INT);")
        applied = await run_migrations(conn, directory)
        assert [m.version for m in applied] == [1, 2, 3]


async def test_modified_applied_migration_is_an_error(pg_database, tmp_path):
    directory = _with_init(tmp_path, **{"0002_x.sql": "CREATE TABLE x (id INT);"})
    async with await _connect(pg_database.dsn) as conn:
        await run_migrations(conn, directory)
        (directory / "0002_x.sql").write_text("CREATE TABLE x (id BIGINT);")
        with pytest.raises(MigrationError, match="0002_x.sql was modified"):
            await run_migrations(conn, directory)


@pytest.mark.parametrize("bad_name", ["1_short.sql", "0002-dash.sql", "0002_Upper.sql"])
def test_bad_filename_rejected(tmp_path, bad_name):
    directory = _write(tmp_path / "m", {bad_name: "SELECT 1;"})
    with pytest.raises(MigrationError, match="NNNN_name.sql"):
        load_migrations(directory)


def test_duplicate_version_rejected(tmp_path):
    directory = _write(tmp_path / "m", {"0002_a.sql": "SELECT 1;", "0002_b.sql": "SELECT 1;"})
    with pytest.raises(MigrationError, match="duplicate"):
        load_migrations(directory)


def test_shipped_migrations_are_well_formed():
    migrations = load_migrations()
    assert migrations[0].filename == "0001_init.sql"
    assert [m.version for m in migrations] == list(range(1, len(migrations) + 1))


async def test_user_uids_start_at_20000(pg_database):
    async with await _connect(pg_database.dsn) as conn:
        await run_migrations(conn)
        cur = await conn.execute(
            "INSERT INTO users (username, display_name, role, password_hash) "
            "VALUES ('a', 'A', 'member', 'x'), ('b', 'B', 'admin', 'x') RETURNING uid"
        )
        assert [row["uid"] for row in await cur.fetchall()] == [20000, 20001]
        with pytest.raises(psycopg.errors.CheckViolation):
            await conn.execute(
                "INSERT INTO users (username, display_name, role, password_hash) "
                "VALUES ('Upper', 'U', 'member', 'x')"
            )

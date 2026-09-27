"""`infra/postgres/db-init.sh`, run the way compose runs it, against the session Postgres.

The session server is already initialized with superuser `homeai` (like the
live `pgdata` volume), so this exercises the "existing volume" path the
postgres image's own init hook can't cover.
"""

import subprocess
from pathlib import Path

import psycopg
import pytest

from app.db.migrate import run_migrations
from tests.conftest import PG_IMAGE, SUPERUSER, SUPERUSER_PASSWORD

SCRIPT = Path(__file__).resolve().parents[3] / "infra" / "postgres" / "db-init.sh"


def _run_db_init(pg_server, platform_password: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            "--network",
            f"container:{pg_server.container_id}",
            "--read-only",
            "--tmpfs",
            "/tmp",
            "--cap-drop",
            "ALL",
            "--user",
            "postgres",
            "-v",
            f"{SCRIPT}:/db-init.sh:ro",
            "-e",
            "PGHOST=127.0.0.1",
            "-e",
            f"PGUSER={SUPERUSER}",
            "-e",
            f"PGPASSWORD={SUPERUSER_PASSWORD}",
            "-e",
            f"PGDATABASE={SUPERUSER}",
            "-e",
            f"PLATFORM_DB_PASSWORD={platform_password}",
            "--entrypoint",
            "bash",
            PG_IMAGE,
            "/db-init.sh",
        ],
        capture_output=True,
        text=True,
        check=False,
    )


def _ok(result: subprocess.CompletedProcess) -> None:
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.fixture(scope="module")
def initialized(pg_server):
    _ok(_run_db_init(pg_server, "first-password"))
    return pg_server


def test_rerun_is_idempotent(initialized):
    _ok(_run_db_init(initialized, "first-password"))
    _ok(_run_db_init(initialized, "first-password"))


def test_role_and_database(initialized):
    with psycopg.connect(initialized.database("postgres").dsn) as conn:
        role = conn.execute(
            "SELECT rolcanlogin, rolsuper, rolcreatedb, rolcreaterole "
            "FROM pg_roles WHERE rolname = 'platform'"
        ).fetchone()
        assert role == (True, False, False, False)
        owner = conn.execute(
            "SELECT pg_get_userbyid(datdba) FROM pg_database WHERE datname = 'homeai_platform'"
        ).fetchone()
        assert owner == ("platform",)


async def test_platform_role_can_run_migrations(initialized):
    db = initialized.database("homeai_platform", "platform", "first-password")
    async with await psycopg.AsyncConnection.connect(db.dsn, autocommit=True) as conn:
        await run_migrations(conn)
        assert await run_migrations(conn) == []


def test_public_cannot_connect_to_platform_db(initialized):
    with psycopg.connect(initialized.database("postgres").dsn, autocommit=True) as conn:
        conn.execute("DROP ROLE IF EXISTS outsider")
        conn.execute("CREATE ROLE outsider LOGIN PASSWORD 'outsider'")
    with pytest.raises(psycopg.OperationalError, match="permission denied"):
        psycopg.connect(initialized.database("homeai_platform", "outsider", "outsider").dsn)


def test_rerun_rotates_password(initialized):
    _ok(_run_db_init(initialized, "rotated-password"))
    try:
        psycopg.connect(
            initialized.database("homeai_platform", "platform", "rotated-password").dsn
        ).close()
        with pytest.raises(psycopg.OperationalError, match="password authentication failed"):
            psycopg.connect(
                initialized.database("homeai_platform", "platform", "first-password").dsn
            )
    finally:
        _ok(_run_db_init(initialized, "first-password"))


def test_missing_platform_password_fails(pg_server):
    result = _run_db_init(pg_server, "")
    assert result.returncode != 0
    assert "PLATFORM_DB_PASSWORD must be set" in result.stderr

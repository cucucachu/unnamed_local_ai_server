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
AGENT_PASSWORD = "agent-password"


def _run_db_init(
    pg_server,
    platform_password: str,
    agent_password: str = AGENT_PASSWORD,
    database: str = SUPERUSER,
) -> subprocess.CompletedProcess:
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
            f"PGDATABASE={database}",
            "-e",
            f"PLATFORM_DB_PASSWORD={platform_password}",
            "-e",
            f"AGENT_DB_PASSWORD={agent_password}",
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


def _agent_db(server):
    return server.database(SUPERUSER, "agent", AGENT_PASSWORD)


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


# --- the `agent` role (M11-04) ------------------------------------------------


def test_agent_role_is_unprivileged(initialized):
    with psycopg.connect(initialized.database("postgres").dsn) as conn:
        role = conn.execute(
            "SELECT rolcanlogin, rolsuper, rolcreatedb, rolcreaterole, rolreplication, "
            "rolbypassrls FROM pg_roles WHERE rolname = 'agent'"
        ).fetchone()
        assert role == (True, False, False, False, False, False)
        owner = conn.execute(
            "SELECT pg_get_userbyid(datdba) FROM pg_database WHERE datname = %s", (SUPERUSER,)
        ).fetchone()
        assert owner == (SUPERUSER,)



def test_rls_bypass_role_is_set_only_and_cannot_log_in(initialized):
    """#194: agent-server's one way past row-level security."""
    with psycopg.connect(initialized.database("postgres").dsn) as conn:
        role = conn.execute(
            "SELECT rolcanlogin, rolsuper, rolcreatedb, rolcreaterole, rolreplication, "
            "rolbypassrls FROM pg_roles WHERE rolname = 'agent_rls_bypass'"
        ).fetchone()
        assert role == (False, False, False, False, False, True)
        grant = conn.execute(
            "SELECT m.inherit_option, m.set_option, m.admin_option FROM pg_auth_members m "
            "WHERE m.roleid = 'agent_rls_bypass'::regrole AND m.member = 'agent'::regrole"
        ).fetchone()
        assert grant == (False, True, False)
        owns = conn.execute(
            "SELECT count(*) FROM pg_class WHERE relowner = 'agent_rls_bypass'::regrole"
        ).fetchone()
        assert owns == (0,)
    with psycopg.connect(_agent_db(initialized).dsn) as conn:
        conn.execute("SET ROLE agent_rls_bypass")
        assert conn.execute("SELECT current_user").fetchone() == ("agent_rls_bypass",)
    with pytest.raises(psycopg.OperationalError):
        psycopg.connect(initialized.database(SUPERUSER, "agent_rls_bypass", "").dsn)

@pytest.mark.parametrize(
    ("user", "password", "dbname"),
    [
        ("agent", AGENT_PASSWORD, "homeai_platform"),
        ("agent", AGENT_PASSWORD, "postgres"),
        ("agent", AGENT_PASSWORD, "template1"),
        ("platform", "first-password", SUPERUSER),
        ("outsider", "outsider", SUPERUSER),
    ],
)
def test_each_role_reaches_only_its_own_database(initialized, user, password, dbname):
    with psycopg.connect(initialized.database("postgres").dsn, autocommit=True) as conn:
        conn.execute("DROP ROLE IF EXISTS outsider")
        conn.execute("CREATE ROLE outsider LOGIN PASSWORD 'outsider'")
    with pytest.raises(psycopg.OperationalError, match="permission denied"):
        psycopg.connect(initialized.database(dbname, user, password).dsn)


def test_existing_superuser_objects_are_handed_to_agent(initialized):
    """What agent-server's tables look like on a volume from before M11-04."""
    with psycopg.connect(initialized.database(SUPERUSER).dsn, autocommit=True) as conn:
        conn.execute("""
            DROP TABLE IF EXISTS legacy_threads, legacy_counter CASCADE;
            DROP SEQUENCE IF EXISTS legacy_free_seq;
            DROP TYPE IF EXISTS legacy_mood;
            DROP FUNCTION IF EXISTS legacy_fn();
            CREATE TABLE legacy_threads (id serial PRIMARY KEY, title text NOT NULL);
            CREATE TABLE legacy_counter (id int GENERATED ALWAYS AS IDENTITY, n int);
            CREATE SEQUENCE legacy_free_seq;
            CREATE TYPE legacy_mood AS ENUM ('ok');
            CREATE FUNCTION legacy_fn() RETURNS int LANGUAGE sql AS 'SELECT 1';
            INSERT INTO legacy_threads (title) SELECT 'thread ' || g FROM generate_series(1, 50) g;
        """)
    _ok(_run_db_init(initialized, "first-password"))

    with psycopg.connect(initialized.database(SUPERUSER).dsn) as conn:
        not_agent = conn.execute("""
            SELECT relname, pg_get_userbyid(relowner) FROM pg_class
            WHERE relnamespace = 'public'::regnamespace AND relowner <> 'agent'::regrole
            UNION ALL
            SELECT typname, pg_get_userbyid(typowner) FROM pg_type
            WHERE typnamespace = 'public'::regnamespace AND typowner <> 'agent'::regrole
            UNION ALL
            SELECT proname, pg_get_userbyid(proowner) FROM pg_proc
            WHERE pronamespace = 'public'::regnamespace AND proowner <> 'agent'::regrole
        """).fetchall()
        assert not_agent == []

    with psycopg.connect(_agent_db(initialized).dsn, autocommit=True) as conn:
        assert conn.execute("SELECT count(*) FROM legacy_threads").fetchone() == (50,)
        conn.execute("INSERT INTO legacy_threads (title) VALUES ('new')")
        conn.execute("INSERT INTO legacy_counter (n) VALUES (1)")
        conn.execute("ALTER TABLE legacy_threads ADD COLUMN IF NOT EXISTS owner uuid")
        conn.execute("CREATE INDEX IF NOT EXISTS legacy_threads_owner ON legacy_threads (owner)")
        conn.execute("CREATE TABLE IF NOT EXISTS agent_made (id int)")
        conn.execute("DROP TABLE agent_made")
        assert conn.execute("SELECT nextval('legacy_free_seq'), legacy_fn()").fetchone() == (1, 1)
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute("DROP DATABASE homeai_platform")
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute(f"ALTER DATABASE {SUPERUSER} OWNER TO agent")


def test_missing_agent_password_fails(pg_server):
    result = _run_db_init(pg_server, "first-password", agent_password="")
    assert result.returncode != 0
    assert "AGENT_DB_PASSWORD must be set" in result.stderr


def test_refuses_a_shared_database_for_the_agent(pg_server):
    result = _run_db_init(pg_server, "first-password", database="homeai_platform")
    assert result.returncode != 0
    assert "POSTGRES_DB must name agent-server's own database" in result.stderr

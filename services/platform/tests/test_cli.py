"""The recovery CLI, run in-process against a migrated ephemeral database."""

import asyncio
import io
import json

import psycopg
import pytest
from psycopg.rows import dict_row

from app import cli
from app.core import passwords
from app.db.migrate import run_migrations
from tests.conftest import make_settings


@pytest.fixture
def settings(pg_database, tmp_path):
    async def migrate():
        async with await psycopg.AsyncConnection.connect(pg_database.dsn, autocommit=True) as conn:
            await run_migrations(conn)

    asyncio.run(migrate())
    return make_settings(pg_database, tmp_path)


@pytest.fixture
def run(settings, monkeypatch, capsys):
    def _run(*argv: str, stdin: str = "") -> tuple[int, str, str]:
        monkeypatch.setattr("sys.stdin", io.StringIO(stdin))
        code = cli.main(list(argv), settings)
        out, err = capsys.readouterr()
        return code, out, err

    return _run


def _users(settings) -> dict[str, dict]:
    with psycopg.connect(settings.database_dsn, row_factory=dict_row) as conn:
        rows = conn.execute("SELECT * FROM users").fetchall()
    return {row["username"]: row for row in rows}


def test_create_user_and_list(run, settings):
    code, out, _ = run("create-user", "E2E-Alice", "--password-stdin", stdin="pw for alice\n")
    assert code == 0
    assert "created member e2e-alice" in out
    user = _users(settings)["e2e-alice"]
    assert user["uid"] >= 20000
    assert user["display_name"] == "E2E-Alice"
    assert passwords.verify_password(user["password_hash"], "pw for alice")

    code, out, _ = run(
        "create-user", "boss", "--role", "admin", "--display-name", "The Boss",
        "--password-stdin", stdin="pw for boss\n",
    )  # fmt: skip
    assert code == 0
    assert _users(settings)["boss"]["uid"] == user["uid"] + 1

    code, out, _ = run("list-users", "--json")
    listed = json.loads(out)
    assert [(u["username"], u["role"]) for u in listed] == [
        ("e2e-alice", "member"),
        ("boss", "admin"),
    ]
    assert "password_hash" not in out
    code, out, _ = run("list-users")
    assert "e2e-alice" in out and "boss" in out

    # CLI users never complete bootstrap.
    with psycopg.connect(settings.database_dsn) as conn:
        assert conn.execute("SELECT count(*) FROM platform_state").fetchone() == (0,)


def test_errors_exit_1_with_code(run):
    assert run("create-user", "dup", "--password-stdin", stdin="long enough\n")[0] == 0
    assert run("create-user", "dup", "--password-stdin", stdin="long enough\n")[::2] == (
        1,
        "error: username_taken\n",
    )
    assert (
        run("create-user", "x", "--password-stdin", stdin="short\n")[2] == "error: weak_password\n"
    )
    assert run("set-role", "ghost", "admin")[2] == "error: not_found\n"


def test_reset_password_revokes_sessions_and_can_clear_totp(run, settings):
    run("create-user", "alice", "--password-stdin", stdin="first password\n")
    with psycopg.connect(settings.database_dsn) as conn:
        conn.execute(
            "INSERT INTO sessions (token_hash, user_id, expires_at) "
            "SELECT '\\x00', id, now() + interval '1 day' FROM users"
        )
        conn.execute("UPDATE users SET totp_secret = 'ABCDEF'")

    code, out, _ = run(
        "reset-password", "alice", "--clear-totp", "--password-stdin", stdin="second password\n"
    )
    assert code == 0
    assert "cleared TOTP" in out
    user = _users(settings)["alice"]
    assert passwords.verify_password(user["password_hash"], "second password")
    assert user["totp_secret"] is None
    with psycopg.connect(settings.database_dsn) as conn:
        assert conn.execute("SELECT revoked_at IS NOT NULL FROM sessions").fetchone() == (True,)


def test_roles_disable_and_last_admin(run, settings):
    run("create-user", "boss", "--role", "admin", "--password-stdin", stdin="long enough\n")
    run("create-user", "alice", "--password-stdin", stdin="long enough\n")

    assert run("set-role", "boss", "member")[2] == "error: last_admin\n"
    assert run("disable-user", "boss")[2] == "error: last_admin\n"

    assert run("set-role", "alice", "admin")[1] == "alice is now admin\n"
    assert run("disable-user", "boss")[1] == "boss is now disabled\n"
    assert _users(settings)["boss"]["disabled_at"] is not None
    assert run("enable-user", "boss")[1] == "boss is now active\n"
    assert _users(settings)["boss"]["disabled_at"] is None

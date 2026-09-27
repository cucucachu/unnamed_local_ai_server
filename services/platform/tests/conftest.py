"""Shared fixtures: one throwaway Postgres container per test session.

`pg_server` starts `postgres:17` (same image as the live stack) via the
host's `docker` CLI on a random loopback port, with the same superuser name
as the live volume (`homeai`). Each test that needs a database gets a fresh
one from `pg_database`, dropped afterwards, so tests never share state.

Requires a reachable Docker daemon; the session errors out (rather than
silently skipping) if there isn't one, because the migration tests are only
meaningful against a real Postgres.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import time
import uuid
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path

import psycopg
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from psycopg import sql
from psycopg.conninfo import make_conninfo

from app.core import storage
from app.core.config import Settings
from app.main import create_app

PG_IMAGE = os.environ.get("TEST_PG_IMAGE", "postgres:17")
SUPERUSER = "homeai"
SUPERUSER_PASSWORD = "test-superuser-password"


@dataclass(frozen=True)
class PgDatabase:
    host: str
    port: int
    user: str
    password: str
    dbname: str

    @property
    def dsn(self) -> str:
        return make_conninfo(
            host=self.host,
            port=self.port,
            user=self.user,
            password=self.password,
            dbname=self.dbname,
        )


@dataclass(frozen=True)
class PgServer:
    container_id: str
    host: str
    port: int

    def database(
        self, dbname: str, user: str = SUPERUSER, password: str = SUPERUSER_PASSWORD
    ) -> PgDatabase:
        return PgDatabase(self.host, self.port, user, password, dbname)


def _docker(*args: str) -> str:
    result = subprocess.run(["docker", *args], capture_output=True, text=True, check=True)
    return result.stdout.strip()


@pytest.fixture(scope="session")
def pg_server() -> Iterator[PgServer]:
    if shutil.which("docker") is None:
        pytest.fail("tests need the `docker` CLI to start an ephemeral Postgres")
    container_id = _docker(
        "run", "-d", "--rm",
        "--label", "homeai.test=platform",
        "-e", f"POSTGRES_USER={SUPERUSER}",
        "-e", f"POSTGRES_PASSWORD={SUPERUSER_PASSWORD}",
        "-p", "127.0.0.1::5432",
        PG_IMAGE,
    )  # fmt: skip
    try:
        host, _, port = _docker("port", container_id, "5432/tcp").splitlines()[0].rpartition(":")
        server = PgServer(container_id, host, int(port))
        # The image's initdb phase only listens on a unix socket, so the first
        # successful TCP connection is to the final server.
        deadline = time.monotonic() + 60
        while True:
            try:
                psycopg.connect(server.database("postgres").dsn, connect_timeout=2).close()
                break
            except psycopg.OperationalError:
                if time.monotonic() > deadline:
                    raise
                time.sleep(0.3)
        yield server
    finally:
        subprocess.run(["docker", "rm", "-f", container_id], capture_output=True, check=False)


@pytest.fixture
def pg_database(pg_server: PgServer) -> Iterator[PgDatabase]:
    """A fresh, empty database owned by the superuser."""
    dbname = f"t_{uuid.uuid4().hex[:12]}"
    admin_dsn = pg_server.database("postgres").dsn
    with psycopg.connect(admin_dsn, autocommit=True) as conn:
        conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(dbname)))
    try:
        yield pg_server.database(dbname)
    finally:
        with psycopg.connect(admin_dsn, autocommit=True) as conn:
            conn.execute(
                sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(dbname))
            )


# --- a running app against a fresh database ---------------------------------


def make_settings(db: PgDatabase, data_dir: Path, **overrides) -> Settings:
    """`data_dir` also holds the spaces root, at `data_dir/spaces`."""
    spaces_dir = data_dir / "spaces"
    spaces_dir.mkdir(parents=True, exist_ok=True)
    return Settings(
        platform_db_host=db.host,
        platform_db_port=db.port,
        platform_db_user=db.user,
        platform_db_password=db.password,
        platform_db_name=db.dbname,
        platform_data_dir=data_dir,
        platform_spaces_dir=spaces_dir,
        _env_file=None,
        **overrides,
    )


@pytest.fixture(autouse=True)
def chowns(monkeypatch) -> dict[Path, tuple[int, int]]:
    """Space-dir chowns as {path: (uid, gid)}, recorded instead of performed.

    Tests run unprivileged and can't chown to root:<space gid>;
    `tests/test_storage.py` checks real ownership as root in a container.
    """
    recorded: dict[Path, tuple[int, int]] = {}

    def fake_fchown(fd: int, uid: int, gid: int) -> None:
        recorded[Path(os.readlink(f"/proc/self/fd/{fd}"))] = (uid, gid)

    monkeypatch.setattr(storage, "_fchown", fake_fchown)
    return recorded


@asynccontextmanager
async def running(app: FastAPI) -> AsyncIterator[AsyncClient]:
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://platform") as client:
            yield client


@dataclass
class Platform:
    app: FastAPI
    client: AsyncClient
    db: PgDatabase
    data_dir: Path


@pytest.fixture
async def platform(pg_database: PgDatabase, tmp_path: Path) -> AsyncIterator[Platform]:
    """The app, started (migrated, bootstrap prepared), with an httpx client on it."""
    app = create_app(make_settings(pg_database, tmp_path))
    async with running(app) as client:
        yield Platform(app, client, pg_database, tmp_path)

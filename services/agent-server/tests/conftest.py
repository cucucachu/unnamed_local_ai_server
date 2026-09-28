import asyncio
import os
import shutil
import subprocess
import threading
import time
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass
from pathlib import Path

import psycopg
import pytest
import uvicorn
from httpx import ASGITransport, AsyncClient
from langgraph.checkpoint.memory import MemorySaver

from app.core.config import Settings
from app.db.threads import InMemoryThreadStore
from app.main import create_app
from tests.fake_exec_manager.scripting import FakeExecManager
from tests.fake_exec_manager.server import create_fake_exec_manager_app
from tests.fake_identity import FixedIdentityVerifier
from tests.fake_model.scripting import FakeModel
from tests.fake_model.server import create_fake_model_app
from tests.fake_platform.scripting import FakePlatform
from tests.fake_platform.server import create_fake_platform_app
from tests.fake_web_fetch.scripting import FakeWebFetch
from tests.fake_web_fetch.server import create_fake_web_fetch_app


@pytest.fixture
def test_settings() -> Settings:
    return Settings(
        model_base_url="http://model-runner:8080/v1",
        model_name="test-model",
        exec_manager_url="http://code-exec-manager:8090",
        exec_default_timeout_s=1,
        postgres_password="test",
        _env_file=None,
    )


@pytest.fixture
async def client(test_settings: Settings) -> AsyncIterator[AsyncClient]:
    # `checkpointer_override`/`thread_store_override` keep this fixture off
    # real Postgres entirely (fast, no real-Postgres dependency) rather than
    # the production lifespan's real Postgres connection — see
    # `app.main.create_app`'s docstring.
    app = create_app(
        test_settings,
        checkpointer_override=MemorySaver(),
        thread_store_override=InMemoryThreadStore(),
        identity_verifier_override=FixedIdentityVerifier(),
    )
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        yield ac


class _UvicornThreadServer:
    """Runs a `uvicorn.Server` on an ephemeral port in a dedicated background
    thread with its own event loop.

    A background thread (rather than a task on the test's own event loop) is
    used so the fake model's server loop can't be starved or entangled by
    whatever the test/agent-under-test is doing on the main loop, and so
    teardown is a simple, synchronous join with no risk of leaking a pending
    task on the test's loop.
    """

    def __init__(self, app) -> None:
        config = uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning")
        self.server = uvicorn.Server(config)
        self._thread = threading.Thread(target=self._run, daemon=True)

    def _run(self) -> None:
        asyncio.run(self.server.serve())

    def start(self) -> None:
        self._thread.start()
        deadline = time.monotonic() + 5
        while not self.server.started:
            if time.monotonic() > deadline:
                raise RuntimeError("fake model server did not start within 5s")
            time.sleep(0.01)

    @property
    def port(self) -> int:
        return self.server.servers[0].sockets[0].getsockname()[1]

    def stop(self) -> None:
        self.server.should_exit = True
        self._thread.join(timeout=5)
        if self._thread.is_alive():  # pragma: no cover - defensive
            raise RuntimeError("fake model server thread did not stop within 5s")


@pytest.fixture
def fake_model() -> Iterator[FakeModel]:
    fake = FakeModel()
    app = create_fake_model_app(fake)
    runner = _UvicornThreadServer(app)
    runner.start()
    fake.base_url = f"http://127.0.0.1:{runner.port}/v1"
    try:
        yield fake
    finally:
        runner.stop()


@pytest.fixture
def fake_exec_manager() -> Iterator[FakeExecManager]:
    """A running fake code-exec-manager, bound to a real ephemeral port.

    A real bound port (rather than an in-process `httpx.MockTransport`) is
    used so the exact same fixture works for both a direct-tool unit test
    (`test_execute_code_tool.py`) and an agent-level WS test
    (`test_chat_ws.py`) whose `Settings.exec_manager_url` must be a real,
    dialable URL — same reasoning as `fake_model` above.
    """
    fake = FakeExecManager()
    app = create_fake_exec_manager_app(fake)
    runner = _UvicornThreadServer(app)
    runner.start()
    fake.base_url = f"http://127.0.0.1:{runner.port}"
    try:
        yield fake
    finally:
        runner.stop()


@pytest.fixture
def fake_web_fetch() -> Iterator[FakeWebFetch]:
    """A running fake `web-fetch`, bound to a real ephemeral port — same
    reasoning as `fake_exec_manager` above: a real bound port (rather than
    an in-process `httpx.MockTransport`/`respx`) is what lets the exact same
    fixture back both a direct-tool unit test and an agent-level WS test
    whose `Settings.web_fetch_url` must be a real, dialable URL.
    """
    fake = FakeWebFetch()
    app = create_fake_web_fetch_app(fake)
    runner = _UvicornThreadServer(app)
    runner.start()
    fake.base_url = f"http://127.0.0.1:{runner.port}"
    try:
        yield fake
    finally:
        runner.stop()


@pytest.fixture
def fake_platform() -> Iterator[FakePlatform]:
    """A running fake platform (delegations + files API) on a real ephemeral
    port, so `PlatformFilesBackend` inside an agent-level test dials it for
    real. Point `Settings.platform_url` at `.base_url` and pass
    `delegation_client_override=fake_platform.client()`.
    """
    fake = FakePlatform()
    runner = _UvicornThreadServer(create_fake_platform_app(fake))
    runner.start()
    fake.base_url = f"http://127.0.0.1:{runner.port}"
    try:
        yield fake
    finally:
        runner.stop()


# --- a throwaway Postgres, initialized like the live one ---------------------

PG_IMAGE = os.environ.get("TEST_PG_IMAGE", "postgres:17")
PG_SUPERUSER = "homeai"
PG_SUPERUSER_PASSWORD = "test-superuser-password"
PG_AGENT_PASSWORD = "test-agent-password"
DB_INIT = Path(__file__).resolve().parents[3] / "infra" / "postgres" / "db-init.sh"


@dataclass(frozen=True)
class PgServer:
    host: str
    port: int

    def dsn(self, user: str, password: str) -> str:
        return f"postgresql://{user}:{password}@{self.host}:{self.port}/{PG_SUPERUSER}"

    @property
    def agent_dsn(self) -> str:
        """What agent-server connects as in the live stack."""
        return self.dsn("agent", PG_AGENT_PASSWORD)

    @property
    def super_dsn(self) -> str:
        """For arranging and inspecting rows behind row-level security's back."""
        return self.dsn(PG_SUPERUSER, PG_SUPERUSER_PASSWORD)


def _docker(*args: str) -> str:
    return subprocess.run(
        ["docker", *args], capture_output=True, text=True, check=True
    ).stdout.strip()


@pytest.fixture(scope="session")
def pg_server() -> Iterator[PgServer]:
    """`postgres:17` on a random loopback port, after `infra/postgres/db-init.sh`.

    Superuser `homeai` and database `homeai` like the live volume, so db-init
    creates `agent` and `agent_rls_bypass` exactly as compose runs it. Skips
    the requesting tests when there's no `docker` CLI.
    """
    if shutil.which("docker") is None:
        pytest.skip("no docker CLI to start a throwaway Postgres")
    container = _docker(
        "run", "-d", "--rm",
        "--label", "homeai.test=agent-server",
        "-e", f"POSTGRES_USER={PG_SUPERUSER}",
        "-e", f"POSTGRES_PASSWORD={PG_SUPERUSER_PASSWORD}",
        "-p", "127.0.0.1::5432",
        PG_IMAGE,
    )  # fmt: skip
    try:
        host, _, port = _docker("port", container, "5432/tcp").splitlines()[0].rpartition(":")
        server = PgServer(host, int(port))
        deadline = time.monotonic() + 60
        while True:
            try:
                psycopg.connect(server.super_dsn, connect_timeout=2).close()
                break
            except psycopg.OperationalError:
                if time.monotonic() > deadline:
                    raise
                time.sleep(0.3)
        init = subprocess.run(
            [
                "docker", "run", "--rm", "--network", f"container:{container}",
                "--user", "postgres", "-v", f"{DB_INIT}:/db-init.sh:ro",
                "-e", "PGHOST=127.0.0.1", "-e", f"PGUSER={PG_SUPERUSER}",
                "-e", f"PGPASSWORD={PG_SUPERUSER_PASSWORD}", "-e", f"PGDATABASE={PG_SUPERUSER}",
                "-e", "PLATFORM_DB_PASSWORD=test-platform-password",
                "-e", f"AGENT_DB_PASSWORD={PG_AGENT_PASSWORD}",
                "--entrypoint", "bash", PG_IMAGE, "/db-init.sh",
            ],
            capture_output=True, text=True, check=False,
        )  # fmt: skip
        assert init.returncode == 0, init.stdout + init.stderr
        yield server
    finally:
        subprocess.run(["docker", "rm", "-f", container], capture_output=True, check=False)

import asyncio
import threading
import time
from collections.abc import AsyncIterator, Iterator

import pytest
import uvicorn
from httpx import ASGITransport, AsyncClient
from langgraph.checkpoint.memory import MemorySaver

from app.core.config import Settings
from app.db.threads import InMemoryThreadStore
from app.main import create_app
from tests.fake_exec_manager.scripting import FakeExecManager
from tests.fake_exec_manager.server import create_fake_exec_manager_app
from tests.fake_model.scripting import FakeModel
from tests.fake_model.server import create_fake_model_app
from tests.fake_web_fetch.scripting import FakeWebFetch
from tests.fake_web_fetch.server import create_fake_web_fetch_app


@pytest.fixture
def test_settings() -> Settings:
    return Settings(
        model_base_url="http://model-runner:8080/v1",
        model_name="test-model",
        exec_manager_url="http://code-exec-manager:8090",
        exec_default_timeout_s=1,
        workspace_root="/data/workspace",
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
        test_settings, checkpointer_override=MemorySaver(), thread_store_override=InMemoryThreadStore()
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

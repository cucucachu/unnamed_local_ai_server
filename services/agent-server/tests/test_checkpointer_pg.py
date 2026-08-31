"""Integration test for the real Postgres-backed checkpointer (M3-01).

Proves checkpoint persistence survives a full saver+pool teardown and
rebuild — not just object identity within one process — by running two
fake-model turns on a thread, tearing the saver down completely, building a
brand new one from scratch against the same Postgres, and asserting a third
turn still sees the full prior history.

Requires a real Postgres reachable via the `TEST_PG_DSN` env var. SKIPPED
(not failed) when it's unset, so a plain `uv run pytest` never tries to
connect to a real database.

Run it:

    TEST_PG_DSN=postgresql://user:pass@host:5432/db uv run pytest -m integration

The simplest way to run it for real is from inside the `agent-server`
container, against the compose Postgres — `TEST_PG_DSN` is already set there
via `docker-compose.yml`'s `agent-server.environment` block:

    docker compose exec agent-server uv run pytest -m integration
"""

from __future__ import annotations

import os
import uuid

import pytest

from app.agent.build import build_agent
from app.db.checkpointer import build_postgres_checkpointer
from tests.fake_model.scripting import FakeModel, TextTurn

TEST_PG_DSN = os.environ.get("TEST_PG_DSN")

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not TEST_PG_DSN, reason="TEST_PG_DSN not set - no real Postgres to test against"
    ),
]


async def test_persistence_survives_saver_teardown(fake_model: FakeModel, tmp_path) -> None:
    settings = fake_model.settings(workspace_root=str(tmp_path))
    # Unique per run so repeated test runs against a persistent Postgres
    # (e.g. the real compose volume) don't accumulate cross-run history.
    thread_id = f"pg-integration-{uuid.uuid4()}"
    config = {"configurable": {"thread_id": thread_id}}

    fake_model.queue(TextTurn("first reply"), TextTurn("second reply"))

    pg1 = await build_postgres_checkpointer(TEST_PG_DSN)
    try:
        agent1 = build_agent(settings, pg1.saver)
        await agent1.ainvoke(
            {"messages": [{"role": "user", "content": "message one"}]}, config=config
        )
        await agent1.ainvoke(
            {"messages": [{"role": "user", "content": "message two"}]}, config=config
        )
    finally:
        await pg1.close()

    assert len(fake_model.requests) == 2

    # Fresh saver + pool from scratch, pointed at the same Postgres + thread.
    # No object from `pg1` is reused — this is the actual "survives teardown"
    # assertion, not just proving the checkpointer works within one process.
    fake_model.queue(TextTurn("third reply"))

    pg2 = await build_postgres_checkpointer(TEST_PG_DSN)
    try:
        agent2 = build_agent(settings, pg2.saver)
        await agent2.ainvoke(
            {"messages": [{"role": "user", "content": "message three"}]}, config=config
        )

        third_request_contents = [
            m.get("content") for m in fake_model.requests[-1]["messages"]
        ]
        assert len(fake_model.requests) == 3
        assert any("message one" in (c or "") for c in third_request_contents)
        assert any("first reply" in (c or "") for c in third_request_contents)
        assert any("message two" in (c or "") for c in third_request_contents)
        assert any("second reply" in (c or "") for c in third_request_contents)
        assert any("message three" in (c or "") for c in third_request_contents)
    finally:
        # Leave the DB clean regardless of assertion outcome.
        await pg2.saver.adelete_thread(thread_id)
        await pg2.close()

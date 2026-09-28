"""Integration test for the real Postgres-backed checkpointer (M3-01).

Proves checkpoint persistence survives a full saver+pool teardown and
rebuild — not just object identity within one process — by running two
fake-model turns on a thread, tearing the saver down completely, building a
brand new one from scratch against the same Postgres, and asserting a third
turn still sees the full prior history.

Runs against the throwaway Postgres from `tests/conftest.py`'s `pg_server`,
as `agent` under row-level security, in a thread its user owns. Skipped
without the `docker` CLI.
"""

from __future__ import annotations

import uuid

import pytest

from app.agent.build import build_agent
from app.db import rls
from app.db.checkpointer import build_postgres_checkpointer
from app.db.threads import PgThreadStore
from tests.conftest import PgServer
from tests.fake_model.scripting import FakeModel, TextTurn

pytestmark = pytest.mark.integration


async def test_persistence_survives_saver_teardown(
    fake_model: FakeModel, pg_server: PgServer
) -> None:
    settings = fake_model.settings()
    owner = str(uuid.uuid4())
    rls.bind_user(owner)

    fake_model.queue(TextTurn("first reply"), TextTurn("second reply"))

    pg1 = await build_postgres_checkpointer(pg_server.agent_dsn)
    try:
        thread_id = (await PgThreadStore(pg1.pool).create(owner, None)).id
        config = {"configurable": {"thread_id": thread_id}}
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

    pg2 = await build_postgres_checkpointer(pg_server.agent_dsn)
    try:
        agent2 = build_agent(settings, pg2.saver)
        await agent2.ainvoke(
            {"messages": [{"role": "user", "content": "message three"}]}, config=config
        )

        third_request_contents = [m.get("content") for m in fake_model.requests[-1]["messages"]]
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

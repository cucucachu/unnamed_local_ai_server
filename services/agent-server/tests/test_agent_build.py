"""Tests for the deep agent assembled in `app.agent.build` / `app.main`'s lifespan.

Exercises the app through `create_app()` + `app.router.lifespan_context(app)`
(rather than calling `build_agent` directly) to prove the actual startup hook
described in the ticket works: a `Settings` override passed to `create_app()`
is what the lifespan-built agent ends up using, not whatever `Settings()`
would resolve to from the real environment.
"""

from collections.abc import AsyncIterator

import pytest
from fastapi import FastAPI
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph.state import CompiledStateGraph

from app.core.delegation import Delegation
from app.main import create_app
from tests.fake_identity import FixedIdentityVerifier
from tests.fake_model.scripting import FakeModel, TextTurn, ToolCallTurn
from tests.fake_platform.scripting import FakePlatform


@pytest.fixture
async def agent_app(
    fake_model: FakeModel, fake_platform: FakePlatform
) -> AsyncIterator[FastAPI]:
    settings = fake_model.settings(platform_url=fake_platform.base_url)
    # `checkpointer_override` keeps this fixture on `MemorySaver` (fast, no
    # real Postgres) rather than the production lifespan's real Postgres
    # connection — see `app.main.create_app`'s docstring.
    app = create_app(
        settings,
        checkpointer_override=MemorySaver(),
        identity_verifier_override=FixedIdentityVerifier(),
    )
    async with app.router.lifespan_context(app):
        yield app


@pytest.fixture
def agent(agent_app: FastAPI) -> CompiledStateGraph:
    return agent_app.state.agent


async def test_agent_invoke_plain(fake_model: FakeModel, agent: CompiledStateGraph) -> None:
    fake_model.queue(TextTurn("hi there"))

    result = await agent.ainvoke(
        {"messages": [{"role": "user", "content": "hello"}]},
        config={"configurable": {"thread_id": "t1"}},
    )

    assert result["messages"][-1].content == "hi there"


async def test_agent_file_tool_roundtrip(
    fake_model: FakeModel, fake_platform: FakePlatform, agent: CompiledStateGraph
) -> None:
    # deepagents==0.7.11's `write_file` tool schema (see
    # `deepagents.middleware.filesystem.WriteFileSchema`) takes `file_path`
    # (absolute; `validate_path` normalizes it and requires a leading `/`)
    # and `content`. The path is the platform's virtual path.
    fake_model.queue(
        ToolCallTurn(
            name="write_file", args={"file_path": "/personal/notes.txt", "content": "note body"}
        ),
        TextTurn("done"),
    )
    client = fake_platform.client()
    delegation = await Delegation.obtain(client, "identity", "t2")

    # `hitl_enabled: False` (M8-03): this test is about the tool roundtrip
    # itself, not the approval flow (see `tests/test_chat_ws.py` for HITL
    # coverage) — HITL defaults on, so it must be explicitly disabled here
    # for the tool to execute directly like it always has.
    result = await agent.ainvoke(
        {"messages": [{"role": "user", "content": "write a note"}]},
        config={
            "configurable": {"thread_id": "t2", "hitl_enabled": False, "delegation": delegation}
        },
    )

    assert result["messages"][-1].content == "done"
    assert fake_platform.personal() == {"notes.txt": b"note body"}
    assert [r[2] for r in fake_platform.file_requests] == [delegation.token]

    # The tool-execution loop must have called the model a second time with
    # the tool's result appended, proving the loop (not just the first
    # response) actually ran.
    assert len(fake_model.requests) == 2
    second_request_roles = [m.get("role") for m in fake_model.requests[1]["messages"]]
    assert "tool" in second_request_roles


async def test_memory_same_thread(fake_model: FakeModel, agent: CompiledStateGraph) -> None:
    fake_model.queue(TextTurn("first reply"), TextTurn("second reply"))

    config = {"configurable": {"thread_id": "shared-thread"}}

    await agent.ainvoke(
        {"messages": [{"role": "user", "content": "message one"}]}, config=config
    )
    await agent.ainvoke(
        {"messages": [{"role": "user", "content": "message two"}]}, config=config
    )

    assert len(fake_model.requests) == 2

    second_request_contents = [
        m.get("content") for m in fake_model.requests[-1]["messages"]
    ]
    assert any("message one" in (c or "") for c in second_request_contents)
    assert any("first reply" in (c or "") for c in second_request_contents)


async def test_file_tools_fail_closed_without_a_delegation(
    fake_model: FakeModel, fake_platform: FakePlatform, agent: CompiledStateGraph
) -> None:
    fake_model.queue(
        ToolCallTurn(name="write_file", args={"file_path": "/personal/n.txt", "content": "x"}),
        TextTurn("done"),
    )

    await agent.ainvoke(
        {"messages": [{"role": "user", "content": "write a note"}]},
        config={"configurable": {"thread_id": "t3", "hitl_enabled": False}},
    )

    assert fake_platform.file_requests == []
    assert fake_platform.personal() == {}
    tool_messages = [m for m in fake_model.requests[1]["messages"] if m.get("role") == "tool"]
    assert "no delegation" in tool_messages[-1]["content"]

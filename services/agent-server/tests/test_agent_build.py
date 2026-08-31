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

from app.main import create_app
from tests.fake_model.scripting import FakeModel, TextTurn, ToolCallTurn


@pytest.fixture
async def agent_app(fake_model: FakeModel, tmp_path) -> AsyncIterator[FastAPI]:
    settings = fake_model.settings(workspace_root=str(tmp_path))
    # `checkpointer_override` keeps this fixture on `MemorySaver` (fast, no
    # real Postgres) rather than the production lifespan's real Postgres
    # connection — see `app.main.create_app`'s docstring.
    app = create_app(settings, checkpointer_override=MemorySaver())
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
    fake_model: FakeModel, agent: CompiledStateGraph, tmp_path
) -> None:
    # deepagents==0.7.11's `write_file` tool schema (see
    # `deepagents.middleware.filesystem.WriteFileSchema`, confirmed via
    # `WriteFileSchema.model_json_schema()`) takes `file_path` (str, "Absolute
    # path where the file should be written. Must be absolute, not relative.")
    # and `content` (str). With `FilesystemBackend(virtual_mode=True)`, "absolute"
    # means a virtual path anchored at `root_dir` (see
    # `deepagents.middleware.filesystem.validate_path`, which normalizes and
    # requires a leading `/`) — so `/notes.txt` resolves to `<root_dir>/notes.txt`
    # on real disk, not a real filesystem-root path.
    fake_model.queue(
        ToolCallTurn(name="write_file", args={"file_path": "/notes.txt", "content": "note body"}),
        TextTurn("done"),
    )

    result = await agent.ainvoke(
        {"messages": [{"role": "user", "content": "write a note"}]},
        config={"configurable": {"thread_id": "t2"}},
    )

    assert result["messages"][-1].content == "done"

    written = tmp_path / "notes.txt"
    assert written.exists()
    assert written.read_text() == "note body"

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

"""The agent's context budget: summarization sized from the model's window,
llama-server overflows routed to deepagents' summarize-and-retry, compact
tool-argument errors, and per-thread scratch paths for offloaded content."""

from __future__ import annotations

import pytest
from deepagents.middleware.summarization import compute_summarization_defaults
from langchain_core.exceptions import ContextOverflowError
from langchain_core.messages import HumanMessage

from app.agent.model_client import build_model
from app.agent.tool_errors import compact_invocation_error
from tests.fake_model.scripting import ErrorTurn, FakeModel, TextTurn, ToolCallTurn
from tests.test_chat_ws import (
    _assert_turn_end,
    _drain_turn,
    _make_client,
    _no_hitl_settings_store,
)

LLAMA_OVERFLOW = {
    "error": {
        "code": 400,
        "message": "request (32880 tokens) exceeds the available context size (32768 tokens), "
        "try increasing it",
        "type": "exceed_context_size_error",
        "n_prompt_tokens": 32880,
        "n_ctx": 32768,
    }
}


def test_profile_sizes_summarization_from_the_context_setting(fake_model: FakeModel) -> None:
    model = build_model(fake_model.settings(agent_context_tokens=65536))
    assert model.profile == {"max_input_tokens": 65536}
    assert compute_summarization_defaults(model)["trigger"] == ("fraction", 0.85)


def test_a_slow_prompt_gets_the_whole_model_timeout(fake_model: FakeModel) -> None:
    model = build_model(fake_model.settings(model_timeout_s=1800))
    assert model.stream_chunk_timeout == 1800


@pytest.mark.parametrize("streaming", [True, False])
async def test_llama_overflow_is_a_context_overflow_error(
    fake_model: FakeModel, streaming: bool
) -> None:
    fake_model.queue(ErrorTurn(400, LLAMA_OVERFLOW))
    model = build_model(fake_model.settings()).model_copy(update={"streaming": streaming})
    with pytest.raises(ContextOverflowError):
        await model.ainvoke([HumanMessage("hi")])


async def test_other_bad_requests_are_not_overflows(fake_model: FakeModel) -> None:
    fake_model.queue(ErrorTurn(400, {"error": {"code": 400, "message": "bad", "type": "x"}}))
    with pytest.raises(Exception) as caught:
        await build_model(fake_model.settings()).ainvoke([HumanMessage("hi")])
    assert not isinstance(caught.value, ContextOverflowError)


async def test_an_overflow_retries_instead_of_ending_the_turn(fake_model, fake_platform) -> None:
    fake_model.queue(ErrorTurn(400, LLAMA_OVERFLOW), TextTurn("done"))
    with (
        _make_client(
            fake_model, fake_platform, settings_store=await _no_hitl_settings_store()
        ) as client,
        client.websocket_connect("/ws/chat/overflow-thread") as ws,
    ):
        ws.send_json({"type": "user_message", "content": "hi"})
        frames = _drain_turn(ws)
    _assert_turn_end(frames[-1], "completed")
    assert not [f for f in frames if f["type"] == "error"]


def test_compact_invocation_error() -> None:
    raw = (
        "Error invoking tool 'edit_file' with kwargs {'file_path': '/personal/a.tsx', "
        "'new_string': \"" + "x" * 5000 + '"} with error:\n'
        " old_string: Field required\n Please fix the error and try again."
    )
    assert compact_invocation_error(raw) == (
        "Error: invalid arguments for edit_file:\nold_string: Field required\n"
        "Call it again with valid arguments."
    )
    assert compact_invocation_error("Error: String not found in file") is None


async def test_invalid_tool_arguments_are_not_echoed_back(fake_model, fake_platform) -> None:
    big = "y" * 3000
    fake_model.queue(
        ToolCallTurn(name="edit_file", args={"file_path": "/personal/a.txt", "new_string": big}),
        TextTurn("done"),
    )
    with (
        _make_client(
            fake_model, fake_platform, settings_store=await _no_hitl_settings_store()
        ) as client,
        client.websocket_connect("/ws/chat/bad-args-thread") as ws,
    ):
        ws.send_json({"type": "user_message", "content": "edit it"})
        _assert_turn_end(_drain_turn(ws)[-1], "completed")

    tool_msg = fake_model.requests[-1]["messages"][-1]
    assert tool_msg["role"] == "tool"
    assert "old_string" in tool_msg["content"]
    assert big not in tool_msg["content"]


async def test_scratch_paths_live_in_the_thread_not_the_users_files(
    fake_model, fake_platform
) -> None:
    fake_model.queue(
        ToolCallTurn(
            name="write_file",
            args={"file_path": "/large_tool_results/r1", "content": "kept aside"},
        ),
        ToolCallTurn(name="read_file", args={"file_path": "/large_tool_results/r1"}),
        TextTurn("done"),
    )
    with (
        _make_client(
            fake_model, fake_platform, settings_store=await _no_hitl_settings_store()
        ) as client,
        client.websocket_connect("/ws/chat/scratch-thread") as ws,
    ):
        ws.send_json({"type": "user_message", "content": "go"})
        _assert_turn_end(_drain_turn(ws)[-1], "completed")

    assert "kept aside" in fake_model.requests[-1]["messages"][-1]["content"]
    assert fake_platform.personal() == {}

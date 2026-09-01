"""Unit tests for `app.agent.execute_code_tool.make_execute_code_tool`.

Exercises the tool directly (`.ainvoke(...)`, not through the full agent
graph) against the `fake_exec_manager` fixture (`tests/fake_exec_manager/`)
— the same fixture the agent-level WS test in `test_chat_ws.py` drives, so
both layers are verified against one shared fake HTTP contract.
"""

import socket

from app.agent.execute_code_tool import make_execute_code_tool
from app.core.config import Settings
from tests.fake_exec_manager.scripting import FakeExecManager


def _settings(exec_manager_url: str) -> Settings:
    return Settings(exec_manager_url=exec_manager_url, _env_file=None)


async def test_ensure_and_execute_called_with_thread_id_as_session(
    fake_exec_manager: FakeExecManager,
) -> None:
    tool = make_execute_code_tool(_settings(fake_exec_manager.base_url))

    await tool.ainvoke(
        {"command": "echo hi"}, config={"configurable": {"thread_id": "thread-abc-123"}}
    )

    assert fake_exec_manager.ensure_calls == ["thread-abc-123"]
    assert len(fake_exec_manager.execute_calls) == 1
    assert fake_exec_manager.execute_calls[0].session_id == "thread-abc-123"
    assert fake_exec_manager.execute_calls[0].command == "echo hi"


async def test_success_result_exact_formatting(fake_exec_manager: FakeExecManager) -> None:
    fake_exec_manager.execute_response = {
        "stdout": "hello\n",
        "stderr": "",
        "exit_code": 0,
        "timed_out": False,
        "duration_ms": 12,
        "truncated": False,
    }
    tool = make_execute_code_tool(_settings(fake_exec_manager.base_url))

    result = await tool.ainvoke(
        {"command": "echo hello"}, config={"configurable": {"thread_id": "fmt-thread"}}
    )

    assert result == (
        "exit_code: 0\n" "--- stdout ---\n" "hello\n\n" "--- stderr ---\n" "(empty)"
    )


async def test_timed_out_truncated_and_empty_placeholders_exact_formatting(
    fake_exec_manager: FakeExecManager,
) -> None:
    fake_exec_manager.execute_response = {
        "stdout": "",
        "stderr": "",
        "exit_code": 124,
        "timed_out": True,
        "duration_ms": 999,
        "truncated": True,
    }
    tool = make_execute_code_tool(_settings(fake_exec_manager.base_url))

    result = await tool.ainvoke(
        {"command": "sleep 1000"}, config={"configurable": {"thread_id": "timeout-thread"}}
    )

    assert result == (
        "exit_code: 124 (TIMED OUT)\n"
        "--- stdout ---\n"
        "(empty)\n"
        "--- stderr ---\n"
        "(empty)\n"
        "[output truncated]"
    )


async def test_stderr_only_exact_formatting(fake_exec_manager: FakeExecManager) -> None:
    fake_exec_manager.execute_response = {
        "stdout": "",
        "stderr": "boom\n",
        "exit_code": 1,
        "timed_out": False,
        "duration_ms": 5,
        "truncated": False,
    }
    tool = make_execute_code_tool(_settings(fake_exec_manager.base_url))

    result = await tool.ainvoke(
        {"command": "false"}, config={"configurable": {"thread_id": "stderr-thread"}}
    )

    assert result == (
        "exit_code: 1\n" "--- stdout ---\n" "(empty)\n" "--- stderr ---\n" "boom\n"
    )


async def test_timeout_seconds_clamped_low(fake_exec_manager: FakeExecManager) -> None:
    tool = make_execute_code_tool(_settings(fake_exec_manager.base_url))

    await tool.ainvoke(
        {"command": "echo hi", "timeout_seconds": 0},
        config={"configurable": {"thread_id": "clamp-low-thread"}},
    )

    assert fake_exec_manager.execute_calls[0].timeout_seconds == 1


async def test_timeout_seconds_clamped_high(fake_exec_manager: FakeExecManager) -> None:
    tool = make_execute_code_tool(_settings(fake_exec_manager.base_url))

    await tool.ainvoke(
        {"command": "echo hi", "timeout_seconds": 999_999},
        config={"configurable": {"thread_id": "clamp-high-thread"}},
    )

    assert fake_exec_manager.execute_calls[0].timeout_seconds == 600


async def test_timeout_seconds_within_range_passed_through(
    fake_exec_manager: FakeExecManager,
) -> None:
    tool = make_execute_code_tool(_settings(fake_exec_manager.base_url))

    await tool.ainvoke(
        {"command": "echo hi", "timeout_seconds": 45},
        config={"configurable": {"thread_id": "clamp-noop-thread"}},
    )

    assert fake_exec_manager.execute_calls[0].timeout_seconds == 45


async def test_unreachable_exec_manager_returns_failure_string_not_exception() -> None:
    # A bound-then-closed socket's port has nothing listening on it, so a
    # connection there fails fast with a real `httpx.ConnectError` rather
    # than hanging.
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        unused_port = probe.getsockname()[1]

    tool = make_execute_code_tool(_settings(f"http://127.0.0.1:{unused_port}"))

    result = await tool.ainvoke(
        {"command": "echo hi"}, config={"configurable": {"thread_id": "unreachable-thread"}}
    )

    assert isinstance(result, str)
    assert result.startswith("execute_code failed: ")


async def test_5xx_from_exec_manager_returns_failure_string_not_exception(
    fake_exec_manager: FakeExecManager,
) -> None:
    fake_exec_manager.execute_status_code = 502
    tool = make_execute_code_tool(_settings(fake_exec_manager.base_url))

    result = await tool.ainvoke(
        {"command": "echo hi"}, config={"configurable": {"thread_id": "server-error-thread"}}
    )

    assert isinstance(result, str)
    assert result.startswith("execute_code failed: ")


async def test_missing_thread_id_falls_back_to_default_session(
    fake_exec_manager: FakeExecManager,
) -> None:
    tool = make_execute_code_tool(_settings(fake_exec_manager.base_url))

    await tool.ainvoke({"command": "echo hi"}, config={"configurable": {}})

    assert fake_exec_manager.ensure_calls == ["default"]


async def test_thread_id_sanitized_for_disallowed_characters(
    fake_exec_manager: FakeExecManager,
) -> None:
    tool = make_execute_code_tool(_settings(fake_exec_manager.base_url))

    await tool.ainvoke(
        {"command": "echo hi"},
        config={"configurable": {"thread_id": "weird/thread id!@#"}},
    )

    assert fake_exec_manager.ensure_calls == ["weirdthreadid"]

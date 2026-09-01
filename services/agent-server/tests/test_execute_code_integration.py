"""Integration test for `execute_code` against the REAL, already-running
compose stack (real Gemma via `model-runner`, real `code-exec-manager`
container lifecycle) — a level up from `test_execute_code_tool.py`'s
fake-exec-manager unit tests and `test_chat_ws.py`'s fake-model agent-level
test above.

SKIPPED (not failed) when `http://localhost/api/health` isn't reachable, so a
plain `uv run pytest` (stack not up) never tries to hit a real network
service — same intent as `test_checkpointer_pg.py`'s `TEST_PG_DSN` skip, but
driven by a live reachability probe instead of an env var, since this test
needs the FULL stack (caddy + agent-server + model-runner + code-exec-manager)
rather than just a Postgres DSN.

Uses the `websockets` package directly (same connect/send/recv pattern as
`scripts/ws_smoke.py`) rather than shelling out to that script — `websockets`
is already present in this project's resolved environment as a transitive
dependency of `uvicorn[standard]` (see `pyproject.toml`), so no new direct
dependency/lockfile change is needed for a pytest-native WS client here.

This is a REAL LLM call (Gemma via the real model-runner), so it's
nondeterministic: one retry on failure, same policy as
`scripts/e2e/gate_m2.sh`/`scripts/e2e/gate_m3.sh`.

Run it (stack already up):

    cd services/agent-server && uv run pytest -m integration -q

Or, more reliably (avoids any host/compose-network hostname mismatch — see
`test_checkpointer_pg.py`'s own docstring for the same advice, and note the
running `agent-server` container must have been rebuilt+recreated to pick up
this test file / the `execute_code` tool source in the first place):

    docker compose exec agent-server uv run pytest -m integration -q
"""

from __future__ import annotations

import asyncio
import json
import uuid

import httpx
import pytest
from websockets.asyncio.client import connect

API_BASE = "http://localhost/api"
WS_BASE = "ws://localhost/ws/chat"
WS_TURN_TIMEOUT_S = 90


def _stack_reachable() -> bool:
    try:
        response = httpx.get(f"{API_BASE}/health", timeout=3)
        return response.status_code == 200
    except httpx.HTTPError:
        return False


pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not _stack_reachable(),
        reason="http://localhost/api/health not reachable - no real stack to test against",
    ),
]


async def _run_ws_turn(thread_id: str, prompt: str) -> list[dict]:
    """Send one `user_message` and collect frames through `turn_end`/`error`."""
    frames: list[dict] = []
    async with connect(f"{WS_BASE}/{thread_id}") as ws:
        await ws.send(json.dumps({"type": "user_message", "content": prompt}))
        async with asyncio.timeout(WS_TURN_TIMEOUT_S):
            async for raw in ws:
                frame = json.loads(raw)
                frames.append(frame)
                if frame["type"] in ("turn_end", "error"):
                    break
    return frames


def _execute_code_tool_end_with_42(frames: list[dict]) -> dict | None:
    for frame in frames:
        if (
            frame.get("type") == "tool_end"
            and frame.get("name") == "execute_code"
            and "42" in frame.get("result_preview", "")
        ):
            return frame
    return None


async def test_execute_code_real_stack_runs_python_and_reports_output() -> None:
    thread_id = f"exec-integration-{uuid.uuid4()}"
    prompt = "Use execute_code to run: python3 -c 'print(21*2)' and tell me the output."

    try:
        frames = await _run_ws_turn(thread_id, prompt)
        match = _execute_code_tool_end_with_42(frames)
        if match is None:
            # LLM nondeterminism allowance - one retry, same policy as the
            # `scripts/e2e/gate_m*.sh` scripts.
            frames = await _run_ws_turn(thread_id, prompt)
            match = _execute_code_tool_end_with_42(frames)

        assert match is not None, (
            "no execute_code tool_end frame with '42' in result_preview after "
            f"2 attempts. Last turn's frames: {frames!r}"
        )
        assert match["status"] == "success", match
        assert not any(f["type"] == "error" for f in frames), frames
    finally:
        # Best-effort cleanup so repeated runs don't accumulate threads -
        # never let a cleanup failure mask the real assertion result above.
        try:
            async with httpx.AsyncClient() as client:
                await client.delete(f"{API_BASE}/threads/{thread_id}")
        except httpx.HTTPError:
            pass

"""Unit tests for `/api/threads*` REST routes (`app/api/chat.py`, M3-02).

Uses `InMemoryThreadStore` (dict-backed, no real Postgres — this test suite
has none, see `tests/test_checkpointer_pg.py`'s own docstring) plus a
`MemorySaver` checkpointer, both injected via `create_app`'s
`checkpointer_override`/`thread_store_override` params exactly like the
existing `test_agent_build.py`/`test_chat_ws.py` fixtures.

Unlike `tests/conftest.py`'s plain `client` fixture (which wraps `app` in an
`ASGITransport` WITHOUT running FastAPI's lifespan — fine for
`test_health.py`, which never touches `app.state.agent`/`app.state.
thread_store`), these tests need the lifespan to actually run so those two
`app.state` attributes exist. `rest_app` below mirrors
`test_agent_build.py`'s `agent_app` fixture (`async with
app.router.lifespan_context(app): yield app`) for exactly that reason.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from langgraph.checkpoint.memory import MemorySaver

from app.db.threads import InMemoryThreadStore
from app.main import create_app
from tests.fake_model.scripting import FakeModel, TextTurn, ToolCallTurn

_UNKNOWN_THREAD_ID = "00000000-0000-0000-0000-000000000000"


@pytest.fixture
async def rest_app(fake_model: FakeModel, tmp_path) -> AsyncIterator[FastAPI]:
    settings = fake_model.settings(workspace_root=str(tmp_path))
    app = create_app(
        settings,
        checkpointer_override=MemorySaver(),
        thread_store_override=InMemoryThreadStore(),
    )
    async with app.router.lifespan_context(app):
        yield app


@pytest.fixture
async def rest_client(rest_app: FastAPI) -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=rest_app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        yield ac


async def test_create_thread_default_title(rest_client: AsyncClient) -> None:
    response = await rest_client.post("/api/threads", json={})

    assert response.status_code == 201
    body = response.json()
    assert body["title"] == "New chat"
    assert isinstance(body["id"], str) and body["id"]
    assert body["created_at"] == body["updated_at"]


async def test_create_thread_custom_title(rest_client: AsyncClient) -> None:
    response = await rest_client.post("/api/threads", json={"title": "Trip planning"})

    assert response.status_code == 201
    assert response.json()["title"] == "Trip planning"


async def test_create_thread_no_body_at_all(rest_client: AsyncClient) -> None:
    # `POST /api/threads` with an entirely absent body (no `{}` even) must
    # still default `title` to `None` -> `"New chat"`, not 422.
    response = await rest_client.post("/api/threads")

    assert response.status_code == 201
    assert response.json()["title"] == "New chat"


async def test_list_threads_ordered_by_updated_at_desc(rest_client: AsyncClient) -> None:
    first = (await rest_client.post("/api/threads", json={"title": "first"})).json()
    second = (await rest_client.post("/api/threads", json={"title": "second"})).json()

    response = await rest_client.get("/api/threads")

    assert response.status_code == 200
    ids = [t["id"] for t in response.json()]
    assert ids == [second["id"], first["id"]]


async def test_delete_thread_then_gone_from_list_and_messages_404(rest_client: AsyncClient) -> None:
    created = (await rest_client.post("/api/threads", json={})).json()
    thread_id = created["id"]

    delete_response = await rest_client.delete(f"/api/threads/{thread_id}")
    assert delete_response.status_code == 204

    list_response = await rest_client.get("/api/threads")
    assert thread_id not in [t["id"] for t in list_response.json()]

    messages_response = await rest_client.get(f"/api/threads/{thread_id}/messages")
    assert messages_response.status_code == 404


async def test_delete_thread_is_idempotent(rest_client: AsyncClient) -> None:
    created = (await rest_client.post("/api/threads", json={})).json()
    thread_id = created["id"]

    assert (await rest_client.delete(f"/api/threads/{thread_id}")).status_code == 204
    # Second delete of an already-gone row must still be 204, not 404/500.
    assert (await rest_client.delete(f"/api/threads/{thread_id}")).status_code == 204


async def test_delete_unknown_thread_is_204(rest_client: AsyncClient) -> None:
    response = await rest_client.delete(f"/api/threads/{_UNKNOWN_THREAD_ID}")
    assert response.status_code == 204


async def test_get_messages_unknown_thread_is_404(rest_client: AsyncClient) -> None:
    response = await rest_client.get(f"/api/threads/{_UNKNOWN_THREAD_ID}/messages")

    assert response.status_code == 404
    assert "detail" in response.json()


async def test_get_messages_row_exists_no_checkpoint_yet_is_empty_list(
    rest_client: AsyncClient,
) -> None:
    created = (await rest_client.post("/api/threads", json={})).json()

    response = await rest_client.get(f"/api/threads/{created['id']}/messages")

    assert response.status_code == 200
    assert response.json() == []


async def test_get_messages_normalizes_tool_call_turn(
    rest_app: FastAPI, rest_client: AsyncClient, fake_model: FakeModel
) -> None:
    created = (await rest_client.post("/api/threads", json={})).json()
    thread_id = created["id"]

    fake_model.queue(
        ToolCallTurn(name="write_file", args={"file_path": "/x.txt", "content": "y"}),
        TextTurn("done"),
    )
    # Drives the turn directly through the same agent/checkpointer the REST
    # app uses (`rest_app.state.agent`), rather than through the WS endpoint
    # — this test is about `GET .../messages` normalization, not the WS
    # wire format (already covered by `test_chat_ws.py`).
    # `hitl_enabled: False` — this test is about `GET .../messages`
    # normalization, not M8-03's approval flow; disabling HITL here keeps it
    # exercising direct tool execution like it always has.
    await rest_app.state.agent.ainvoke(
        {"messages": [{"role": "user", "content": "write a file"}]},
        config={"configurable": {"thread_id": thread_id, "hitl_enabled": False}},
    )

    response = await rest_client.get(f"/api/threads/{thread_id}/messages")

    assert response.status_code == 200
    messages = response.json()
    assert [m["role"] for m in messages] == ["user", "assistant", "tool", "assistant"]

    user_msg, tool_call_msg, tool_result_msg, final_msg = messages

    assert user_msg["content"] == "write a file"
    assert user_msg["tool_calls"] is None
    assert user_msg["tool_name"] is None

    assert tool_call_msg["content"] == ""
    assert tool_call_msg["tool_name"] is None
    assert tool_call_msg["tool_calls"] == [
        {"id": "call_1", "name": "write_file", "args": {"file_path": "/x.txt", "content": "y"}}
    ]

    assert tool_result_msg["tool_name"] == "write_file"
    assert tool_result_msg["content"] == "Updated file /x.txt"
    assert tool_result_msg["tool_calls"] is None
    assert tool_result_msg["tool_call_id"] == "call_1"

    assert user_msg["tool_call_id"] is None
    assert tool_call_msg["tool_call_id"] is None
    assert final_msg["tool_call_id"] is None

    assert final_msg["content"] == "done"
    assert final_msg["tool_calls"] is None
    assert final_msg["tool_name"] is None

    # Every normalized message has a non-empty string id.
    assert all(isinstance(m["id"], str) and m["id"] for m in messages)

    # ainvoke (not the WS path) does not write `turn_stats`, so no `turn`
    # metadata is attached — the same shape older history rows keep.
    assert all(m.get("turn") is None for m in messages)


async def test_get_messages_attaches_turn_metadata_on_final_assistant(
    rest_app: FastAPI, rest_client: AsyncClient, fake_model: FakeModel
) -> None:
    """M9-02: `MessageOut.turn` is hydrated from `turn_stats` onto the final
    assistant row of a turn; other rows stay `turn: null`."""
    from datetime import UTC, datetime

    from app.db.turn_stats import TurnStat

    created = (await rest_client.post("/api/threads", json={})).json()
    thread_id = created["id"]

    fake_model.queue(TextTurn("plain reply"))
    await rest_app.state.agent.ainvoke(
        {"messages": [{"role": "user", "content": "hi"}]},
        config={"configurable": {"thread_id": thread_id, "hitl_enabled": False}},
    )

    state = await rest_app.state.agent.aget_state({"configurable": {"thread_id": thread_id}})
    last_ai = next(m for m in reversed(state.values["messages"]) if getattr(m, "type", None) == "ai")
    await rest_app.state.turn_stats_store.upsert(
        TurnStat(
            thread_id=thread_id,
            final_message_id=last_ai.id,
            status="completed",
            duration_ms=1234,
            started_at=datetime.now(UTC),
        )
    )

    response = await rest_client.get(f"/api/threads/{thread_id}/messages")
    assert response.status_code == 200
    messages = response.json()
    assert [m["role"] for m in messages] == ["user", "assistant"]
    assert messages[0]["turn"] is None
    assert messages[1]["turn"] == {"status": "completed", "duration_ms": 1234}


async def test_branches_empty_without_fork_and_put_rejects_non_tip(
    rest_app: FastAPI, rest_client: AsyncClient, fake_model: FakeModel
) -> None:
    """M8-05: no branch points on a linear thread; PUT 404s a parent checkpoint."""
    from app.api.chat_ws import checkpoint_id_of, list_state_history

    created = (await rest_client.post("/api/threads", json={})).json()
    thread_id = created["id"]

    fake_model.queue(TextTurn("one"), TextTurn("two"))
    await rest_app.state.agent.ainvoke(
        {"messages": [{"role": "user", "content": "turn one"}]},
        config={"configurable": {"thread_id": thread_id, "hitl_enabled": False}},
    )
    await rest_app.state.agent.ainvoke(
        {"messages": [{"role": "user", "content": "turn two"}]},
        config={"configurable": {"thread_id": thread_id, "hitl_enabled": False}},
    )

    empty = await rest_client.get(f"/api/threads/{thread_id}/branches")
    assert empty.status_code == 200
    assert empty.json() == []

    missing_thread = await rest_client.get(f"/api/threads/{_UNKNOWN_THREAD_ID}/branches")
    assert missing_thread.status_code == 404

    snapshots = await list_state_history(rest_app.state.agent, thread_id)
    children: dict[str, list[str]] = {}
    known: list[str] = []
    for snap in snapshots:
        cid = checkpoint_id_of(snap)
        if not cid:
            continue
        known.append(cid)
        parent_id = checkpoint_id_of(snap.parent_config) if snap.parent_config else None
        if parent_id:
            children.setdefault(parent_id, []).append(cid)
    non_tip = next(cid for cid in known if children.get(cid))
    rejected = await rest_client.put(
        f"/api/threads/{thread_id}/active_branch", json={"checkpoint_id": non_tip}
    )
    assert rejected.status_code == 404

    tip = next(cid for cid in known if not children.get(cid))
    accepted = await rest_client.put(
        f"/api/threads/{thread_id}/active_branch", json={"checkpoint_id": tip}
    )
    assert accepted.status_code == 204

"""M10-04: every route but health needs a verified identity; threads and
settings are per user; ownerless threads go to the bootstrap admin.

Runs the real `JwksIdentityVerifier` over a local key pair (`LocalKeyPair`),
so these tests exercise the same verification production uses.
"""

from __future__ import annotations

import re

import httpx
import pytest
import respx
from langgraph.checkpoint.memory import MemorySaver
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.core.config import Settings
from app.core.orphans import OrphanAdopter
from app.db.settings import InMemorySettingsStore
from app.db.threads import InMemoryThreadStore
from app.main import create_app
from tests.fake_identity import LocalKeyPair
from tests.fake_model.scripting import FakeModel, TextTurn

ALICE = "aaaaaaaa-0000-4000-8000-000000000001"
BOB = "bbbbbbbb-0000-4000-8000-000000000002"
AGENT_TOKEN = "agent-secret"


@pytest.fixture
def keys() -> LocalKeyPair:
    return LocalKeyPair()


def _app(settings: Settings, keys: LocalKeyPair, **overrides):
    overrides.setdefault("thread_store_override", InMemoryThreadStore())
    overrides.setdefault("settings_store_override", InMemorySettingsStore())
    return create_app(
        settings,
        checkpointer_override=MemorySaver(),
        identity_verifier_override=keys.verifier(),
        **overrides,
    )


@pytest.fixture
def client(test_settings: Settings, keys: LocalKeyPair):
    with TestClient(_app(test_settings, keys)) as c:
        yield c


def _receive_close_code(ws) -> int:
    with pytest.raises(WebSocketDisconnect) as exc_info:
        ws.receive_json()
    return exc_info.value.code


def _http_routes(app) -> list[tuple[str, str]]:
    routes = []
    for path, operations in app.openapi()["paths"].items():
        if path == "/api/health":
            continue
        routes.extend((method.upper(), re.sub(r"\{[^}]+\}", "x", path)) for method in operations)
    return routes


def test_every_route_except_health_is_authenticated(client: TestClient, keys: LocalKeyPair):
    routes = _http_routes(client.app)
    assert len(routes) >= 15
    forged = LocalKeyPair().headers(ALICE)
    wrong_act = keys.headers(ALICE, act="agent")
    for method, path in routes:
        for headers in ({}, forged, wrong_act, {"X-HomeAI-Identity": "garbage"}):
            response = client.request(method, path, headers=headers)
            assert response.status_code == 401, (method, path, headers, response.text)
            if method != "HEAD":
                assert response.json() == {"detail": "unauthenticated"}


def test_health_is_public(client: TestClient):
    assert client.get("/api/health").json() == {"status": "ok"}


def test_valid_identity_is_accepted(client: TestClient, keys: LocalKeyPair):
    assert client.get("/api/threads", headers=keys.headers(ALICE)).status_code == 200
    assert client.get("/api/settings", headers=keys.headers(ALICE)).status_code == 200


def test_bearer_session_token_alone_is_not_an_identity(client: TestClient):
    response = client.get("/api/threads", headers={"Authorization": "Bearer hs_abc"})
    assert response.status_code == 401


@pytest.mark.parametrize(
    "headers",
    [
        pytest.param({}, id="missing"),
        pytest.param({"X-HomeAI-Identity": "garbage"}, id="garbage"),
        pytest.param(LocalKeyPair().headers(ALICE), id="forged"),
    ],
)
def test_ws_without_valid_identity_closes_4401(client: TestClient, keys: LocalKeyPair, headers):
    thread_id = client.post("/api/threads", headers=keys.headers(ALICE)).json()["id"]
    with client.websocket_connect(f"/ws/chat/{thread_id}", headers=headers) as ws:
        assert _receive_close_code(ws) == 4401


def test_ws_agent_act_closes_4401(client: TestClient, keys: LocalKeyPair):
    thread_id = client.post("/api/threads", headers=keys.headers(ALICE)).json()["id"]
    headers = keys.headers(ALICE, act="agent")
    with client.websocket_connect(f"/ws/chat/{thread_id}", headers=headers) as ws:
        assert _receive_close_code(ws) == 4401


def test_jwks_unreachable_is_503_and_ws_1011(test_settings: Settings, keys: LocalKeyPair):
    keys.fail_fetch = True
    with TestClient(_app(test_settings, keys)) as client:
        response = client.get("/api/threads", headers=keys.headers(ALICE))
        assert response.status_code == 503
        with client.websocket_connect("/ws/chat/x", headers=keys.headers(ALICE)) as ws:
            assert _receive_close_code(ws) == 1011


# --- two users ---------------------------------------------------------------


def test_threads_are_isolated_between_users(
    fake_model: FakeModel, tmp_path, keys: LocalKeyPair
) -> None:
    fake_model.queue(TextTurn("hello alice"))
    settings = fake_model.settings(files_root=str(tmp_path))
    thread_store = InMemoryThreadStore()
    alice, bob = keys.headers(ALICE), keys.headers(BOB)

    with TestClient(_app(settings, keys, thread_store_override=thread_store)) as client:
        created = client.post("/api/threads", json={"title": "Alice's"}, headers=alice)
        assert created.status_code == 201
        thread_id = created.json()["id"]
        assert thread_store._rows[thread_id].owner_user_id == ALICE

        with client.websocket_connect(f"/ws/chat/{thread_id}", headers=alice) as ws:
            ws.send_json({"type": "user_message", "content": "hi"})
            while ws.receive_json()["type"] != "turn_end":
                pass
        alice_messages = client.get(f"/api/threads/{thread_id}/messages", headers=alice).json()
        assert [m["role"] for m in alice_messages] == ["user", "assistant"]

        assert client.get("/api/threads", headers=bob).json() == []
        missing = "00000000-0000-4000-8000-00000000dead"
        for tid in (thread_id, missing):
            for method, path, body in [
                ("GET", f"/api/threads/{tid}/messages", None),
                ("GET", f"/api/threads/{tid}/branches", None),
                ("PUT", f"/api/threads/{tid}/active_branch", {"checkpoint_id": "c"}),
            ]:
                response = client.request(method, path, json=body, headers=bob)
                assert response.status_code == 404, (method, path)
                assert response.json() == {"detail": f"thread '{tid}' not found"}
            state = client.get(f"/api/threads/{tid}/state", headers=bob)
            assert state.json() == {"pending_approval": None}
            assert client.delete(f"/api/threads/{tid}", headers=bob).status_code == 204

            with client.websocket_connect(f"/ws/chat/{tid}", headers=bob) as ws:
                assert _receive_close_code(ws) == 4404

        # Bob's delete was a no-op: Alice still has her thread and its history.
        assert [t["id"] for t in client.get("/api/threads", headers=alice).json()] == [thread_id]
        again = client.get(f"/api/threads/{thread_id}/messages", headers=alice).json()
        assert again == alice_messages

        assert client.delete(f"/api/threads/{thread_id}", headers=alice).status_code == 204
        assert client.get("/api/threads", headers=alice).json() == []


def test_ws_unknown_thread_closes_4404_without_creating_it(client: TestClient, keys):
    with client.websocket_connect("/ws/chat/never-created", headers=keys.headers(ALICE)) as ws:
        assert _receive_close_code(ws) == 4404
    assert client.get("/api/threads", headers=keys.headers(ALICE)).json() == []


def test_settings_are_per_user(client: TestClient, keys: LocalKeyPair):
    alice, bob = keys.headers(ALICE), keys.headers(BOB)
    updated = client.put("/api/settings", json={"hitl_enabled": False}, headers=alice)
    assert updated.json()["hitl_enabled"] is False
    assert client.get("/api/settings", headers=alice).json()["hitl_enabled"] is False
    assert client.get("/api/settings", headers=bob).json()["hitl_enabled"] is True


# --- orphans -----------------------------------------------------------------


def _orphan_app(test_settings: Settings, keys: LocalKeyPair):
    settings = test_settings.model_copy(update={"platform_agent_token": AGENT_TOKEN})
    thread_store = InMemoryThreadStore()
    thread_store.insert("legacy-thread", None, title="Before accounts")
    settings_store = InMemorySettingsStore(legacy={"hitl_enabled": False})
    app = _app(
        settings,
        keys,
        thread_store_override=thread_store,
        settings_store_override=settings_store,
    )
    return app, settings_store


BOOTSTRAP_URL = "http://platform:8100/internal/bootstrap-admin"


@respx.mock
def test_orphans_invisible_while_bootstrap_pending(test_settings: Settings, keys):
    route = respx.get(BOOTSTRAP_URL).mock(
        return_value=httpx.Response(404, json={"detail": "bootstrap_pending"})
    )
    app, settings_store = _orphan_app(test_settings, keys)
    with TestClient(app) as client:
        for user in (ALICE, BOB):
            assert client.get("/api/threads", headers=keys.headers(user)).json() == []
            response = client.get("/api/threads/legacy-thread/messages", headers=keys.headers(user))
            assert response.status_code == 404
    assert route.called
    assert route.calls[0].request.headers["Authorization"] == f"Bearer {AGENT_TOKEN}"
    assert settings_store.legacy == {"hitl_enabled": False}
    assert not app.state.orphan_adopter.done


@respx.mock
def test_orphans_go_to_bootstrap_admin(test_settings: Settings, keys):
    route = respx.get(BOOTSTRAP_URL).mock(return_value=httpx.Response(200, json={"user_id": ALICE}))
    app, settings_store = _orphan_app(test_settings, keys)
    with TestClient(app) as client:
        assert client.get("/api/threads", headers=keys.headers(BOB)).json() == []
        listed = client.get("/api/threads", headers=keys.headers(ALICE)).json()
        assert [(t["id"], t["title"]) for t in listed] == [("legacy-thread", "Before accounts")]
        assert client.get("/api/settings", headers=keys.headers(ALICE)).json()["hitl_enabled"] is False
        assert client.get("/api/settings", headers=keys.headers(BOB)).json()["hitl_enabled"] is True
        client.get("/api/threads", headers=keys.headers(ALICE))
    assert route.call_count == 1
    assert settings_store.legacy == {}


def test_no_agent_token_means_no_adoption(client: TestClient):
    assert client.app.state.orphan_adopter.done


async def test_adopter_retries_after_interval_and_survives_errors():
    thread_store = InMemoryThreadStore()
    thread_store.insert("legacy", None)
    answers: list[object] = [RuntimeError("platform down"), None, ALICE]
    calls = 0

    async def lookup():
        nonlocal calls
        calls += 1
        answer = answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer

    clock = [0.0]
    adopter = OrphanAdopter(
        lookup, thread_store, InMemorySettingsStore(), retry_interval_s=10, clock=lambda: clock[0]
    )
    await adopter.adopt()
    await adopter.adopt()
    assert calls == 1
    clock[0] = 10.0
    await adopter.adopt()
    assert calls == 2 and not adopter.done
    clock[0] = 20.0
    await adopter.adopt()
    assert adopter.done
    assert (await thread_store.get("legacy", ALICE)) is not None
    clock[0] = 100.0
    await adopter.adopt()
    assert calls == 3

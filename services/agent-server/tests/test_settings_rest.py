"""Unit tests for `/api/settings` (`app/api/settings.py`, M8-02).

`app.state.settings_store` is set inside `lifespan` (see
`app/main.py::create_app`), so — unlike `test_files_rest.py`'s fixtures,
which never touch anything lifespan-set — this needs the lifespan to
actually run. Mirrors `test_chat.py`'s `rest_app`/`rest_client` fixture
pair (`async with app.router.lifespan_context(app): yield app`) for exactly
that reason, with an explicit fresh `InMemorySettingsStore` per test so its
state is inspectable/isolated (same reasoning as `test_threads_pg.py`'s own
docstring about `InMemoryThreadStore`).
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from langgraph.checkpoint.memory import MemorySaver

from app.core.config import Settings
from app.db.settings import InMemorySettingsStore
from app.main import create_app


@pytest.fixture
def settings_settings() -> Settings:
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
async def settings_app(settings_settings: Settings) -> AsyncIterator[FastAPI]:
    app = create_app(
        settings_settings,
        checkpointer_override=MemorySaver(),
        settings_store_override=InMemorySettingsStore(),
    )
    async with app.router.lifespan_context(app):
        yield app


@pytest.fixture
async def settings_client(settings_app: FastAPI) -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=settings_app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        yield ac


# ---------------------------------------------------------------------------
# GET — defaults
# ---------------------------------------------------------------------------


async def test_get_defaults_when_nothing_stored(settings_client: AsyncClient) -> None:
    response = await settings_client.get("/api/settings")

    assert response.status_code == 200
    assert response.json() == {
        "hitl_enabled": True,
        "thinking_enabled": False,
        "edit_mode_default": "truncate",
    }


# ---------------------------------------------------------------------------
# PUT — partial update + merge
# ---------------------------------------------------------------------------


async def test_put_partial_merges_and_persists(settings_client: AsyncClient) -> None:
    response = await settings_client.put("/api/settings", json={"hitl_enabled": False})

    assert response.status_code == 200
    assert response.json() == {
        "hitl_enabled": False,
        "thinking_enabled": False,
        "edit_mode_default": "truncate",
    }

    # GET after PUT reflects the change.
    get_response = await settings_client.get("/api/settings")
    assert get_response.json() == response.json()


async def test_put_multiple_fields_at_once(settings_client: AsyncClient) -> None:
    response = await settings_client.put(
        "/api/settings",
        json={"thinking_enabled": True, "edit_mode_default": "fork"},
    )

    assert response.status_code == 200
    assert response.json() == {
        "hitl_enabled": True,
        "thinking_enabled": True,
        "edit_mode_default": "fork",
    }


async def test_put_second_call_only_touches_its_own_fields(settings_client: AsyncClient) -> None:
    await settings_client.put("/api/settings", json={"hitl_enabled": False})
    response = await settings_client.put("/api/settings", json={"thinking_enabled": True})

    assert response.status_code == 200
    assert response.json() == {
        "hitl_enabled": False,  # preserved from the first PUT
        "thinking_enabled": True,
        "edit_mode_default": "truncate",
    }


async def test_put_empty_body_is_a_noop(settings_client: AsyncClient) -> None:
    response = await settings_client.put("/api/settings", json={})

    assert response.status_code == 200
    assert response.json() == {
        "hitl_enabled": True,
        "thinking_enabled": False,
        "edit_mode_default": "truncate",
    }


# ---------------------------------------------------------------------------
# PUT — validation (422s)
# ---------------------------------------------------------------------------


async def test_put_unknown_key_is_422(settings_client: AsyncClient) -> None:
    response = await settings_client.put("/api/settings", json={"nonexistent_key": True})

    assert response.status_code == 422


async def test_put_wrong_type_is_422(settings_client: AsyncClient) -> None:
    response = await settings_client.put("/api/settings", json={"hitl_enabled": "yes"})

    assert response.status_code == 422


async def test_put_invalid_literal_is_422(settings_client: AsyncClient) -> None:
    response = await settings_client.put("/api/settings", json={"edit_mode_default": "replace"})

    assert response.status_code == 422


async def test_put_invalid_request_does_not_persist_partial_state(
    settings_client: AsyncClient,
) -> None:
    """A rejected PUT (422) must not have partially applied any of its
    valid-looking sibling fields — FastAPI validates the whole request body
    before the route function (and thus the store) ever runs, so this is
    really a confirmation of that, not custom code in `app/api/settings.py`.
    """
    response = await settings_client.put(
        "/api/settings", json={"hitl_enabled": False, "nonexistent_key": True}
    )
    assert response.status_code == 422

    get_response = await settings_client.get("/api/settings")
    assert get_response.json()["hitl_enabled"] is True  # untouched

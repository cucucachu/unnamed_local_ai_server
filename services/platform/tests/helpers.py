"""Helpers for driving the accounts API the way Caddy and the clients will."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import psycopg
from psycopg.rows import dict_row

from app.core import users
from tests.conftest import Platform

NATIVE = {"X-HomeAI-Client": "native"}
PASSWORD = "correct horse battery"


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def sql(platform: Platform, query: str, params: Any = None) -> list[dict]:
    with psycopg.connect(platform.db.dsn, autocommit=True, row_factory=dict_row) as conn:
        cur = conn.execute(query, params)
        return cur.fetchall() if cur.description else []


def setup_code(platform: Platform) -> str:
    return (platform.data_dir / "setup-code").read_text().strip()


async def create_user(platform: Platform, username: str, role: str = "member", **kw) -> dict:
    async with platform.app.state.db_pool.connection() as conn:
        return await users.create_user(
            conn,
            username=username,
            display_name=kw.get("display_name", username.title()),
            password=kw.get("password", PASSWORD),
            role=role,
            storage=platform.app.state.storage,
        )


async def login(platform: Platform, username: str, password: str = PASSWORD, **body) -> str:
    """Native login; returns the opaque session token."""
    response = await platform.client.post(
        "/api/auth/login", json={"username": username, "password": password, **body}, headers=NATIVE
    )
    assert response.status_code == 200, response.text
    return response.json()["session_token"]


async def identity(platform: Platform, token: str) -> dict[str, str]:
    """What Caddy's forward_auth would attach for this session: the identity header."""
    response = await platform.client.get("/internal/auth/verify", headers=bearer(token))
    assert response.status_code == 200, response.text
    return {"X-HomeAI-Identity": response.headers["X-HomeAI-Identity"]}


async def step_up(platform: Platform, token: str, password: str = PASSWORD) -> None:
    response = await platform.client.post(
        "/api/auth/step-up", json={"password": password}, headers=bearer(token)
    )
    assert response.status_code == 200, response.text


async def bootstrap_admin(platform: Platform, username: str = "root") -> str:
    """Complete setup; returns the admin's session token."""
    response = await platform.client.post(
        "/api/auth/setup",
        json={
            "setup_code": setup_code(platform),
            "username": username,
            "display_name": "Root",
            "password": PASSWORD,
        },
        headers=NATIVE,
    )
    assert response.status_code == 200, response.text
    return response.json()["session_token"]


async def stepped_up_admin(platform: Platform) -> dict[str, str]:
    """Identity headers for a freshly stepped-up bootstrap admin."""
    token = await bootstrap_admin(platform)
    await step_up(platform, token)
    return await identity(platform, token)


async def delegation(platform: Platform, token: str) -> dict[str, str]:
    """An act=agent bearer for this session, as the delegation endpoint mints."""
    headers = await identity(platform, token)
    claims = platform.app.state.tokens.verify_token(headers["X-HomeAI-Identity"], act="user")
    payload = {k: claims[k] for k in ("sub", "sid", "role")} | {"act": "agent", "thr": "t-1"}
    return bearer(platform.app.state.tokens.issue_token(payload, timedelta(minutes=15)))

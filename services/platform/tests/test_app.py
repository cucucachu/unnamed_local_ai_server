from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import psycopg
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.core.config import Settings
from app.core.tokens import TokenService, load_or_create_signing_key
from app.db.pool import open_pool
from app.main import create_app


def _settings(db, data_dir) -> Settings:
    return Settings(
        platform_db_host=db.host,
        platform_db_port=db.port,
        platform_db_user=db.user,
        platform_db_password=db.password,
        platform_db_name=db.dbname,
        platform_data_dir=data_dir,
        _env_file=None,
    )


@asynccontextmanager
async def _running(app: FastAPI) -> AsyncIterator[AsyncClient]:
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://platform") as client:
            yield client


async def test_startup_migrates_and_serves_health(pg_database, tmp_path):
    async with _running(create_app(_settings(pg_database, tmp_path))) as client:
        response = await client.get("/internal/health")
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}

    with psycopg.connect(pg_database.dsn) as conn:
        versions = conn.execute("SELECT version FROM schema_migrations ORDER BY version").fetchall()
        assert versions == [(1,), (2,)]


async def test_jwks_and_kid_stable_across_restarts(pg_database, tmp_path):
    kids = []
    for _ in range(2):
        app = create_app(_settings(pg_database, tmp_path))
        async with _running(app) as client:
            response = await client.get("/internal/jwks")
            assert response.status_code == 200
            (jwk,) = response.json()["keys"]
            assert jwk["kid"] == app.state.tokens.kid
            kids.append(jwk["kid"])
    assert kids[0] == kids[1]
    assert (tmp_path / "keys" / "signing-key.pem").exists()


async def test_health_reports_unreachable_database(pg_database, tmp_path):
    pool = await open_pool(pg_database.dsn)
    app = create_app(_settings(pg_database, tmp_path), db_pool_override=pool)
    async with _running(app) as client:
        await pool.close()
        response = await client.get("/internal/health")
        assert response.status_code == 503
        assert response.json()["database"] == "unreachable"


async def test_overrides_are_used(pg_database, tmp_path):
    pool = await open_pool(pg_database.dsn)
    tokens = TokenService(load_or_create_signing_key(tmp_path / "elsewhere"))
    try:
        app = create_app(
            _settings(pg_database, tmp_path / "data"),
            db_pool_override=pool,
            token_service_override=tokens,
        )
        async with _running(app) as client:
            assert (await client.get("/internal/jwks")).json() == tokens.jwks()
        assert not (tmp_path / "data" / "keys").exists()
        assert not pool.closed
    finally:
        await pool.close()


async def test_routes(pg_database, tmp_path):
    app = create_app(_settings(pg_database, tmp_path))
    paths = set(app.openapi()["paths"])
    assert {"/internal/health", "/internal/jwks", "/internal/auth/verify"} <= paths
    assert {p.split("/")[1] for p in paths} == {"internal", "api"}
    assert {p.split("/")[2] for p in paths if p.startswith("/api/")} == {"auth", "platform"}

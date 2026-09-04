from collections.abc import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.config import Settings
from app.main import create_app


@pytest.fixture
def test_settings() -> Settings:
    return Settings(
        egress_proxy_url="http://egress-proxy:8080",
        fetch_timeout_s=5,
        fetch_max_bytes=1_000_000,
        fetch_max_text_chars=40_000,
        fetch_max_redirects=5,
        _env_file=None,
    )


@pytest.fixture
async def client(test_settings: Settings) -> AsyncIterator[AsyncClient]:
    app = create_app(test_settings)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        yield ac

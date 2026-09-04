"""FastAPI application factory for web-fetch (M7-03).

`create_app()` builds the ASGI app; the module-level `app` object below is
what the Dockerfile's `uv run uvicorn app.main:app` command serves. No
`/api` prefix, unlike `agent-server` — this is an internal-only service
reached directly at `http://web-fetch:8000/...`, same convention as
`code-exec-manager`'s own unprefixed router.
"""

from fastapi import FastAPI

from app.api import fetch, health
from app.core.config import Settings


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the FastAPI app. `settings` lets tests inject config (e.g. a
    fake `egress_proxy_url`) without touching real env vars/`.env` — same
    escape hatch as `agent-server`'s own `create_app()`."""
    app = FastAPI()
    app.state.settings = settings or Settings()

    app.include_router(health.router)
    app.include_router(fetch.router)

    return app


app = create_app()

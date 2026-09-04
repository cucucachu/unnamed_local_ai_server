"""Minimal ASGI stand-in for the real `web-fetch` service's `/search` and
`/fetch` endpoints, scripted by a `FakeWebFetch` (see `scripting.py`) —
mirrors `tests/fake_exec_manager/server.py`'s pattern.
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from .scripting import FakeWebFetch


def create_fake_web_fetch_app(fake: FakeWebFetch) -> FastAPI:
    app = FastAPI()

    @app.get("/search")
    async def search(q: str, n: str | None = None) -> JSONResponse:
        fake.record_search(q, n)
        return JSONResponse(status_code=fake.search_status_code, content=fake.search_response)

    @app.get("/fetch")
    async def fetch(url: str) -> JSONResponse:
        fake.record_fetch(url)
        return JSONResponse(status_code=fake.fetch_status_code, content=fake.fetch_response)

    return app

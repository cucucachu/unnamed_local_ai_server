"""`GET /search` (spec §3) — SearXNG-backed web search.

Queries the internal SearXNG instance's own JSON API
(`http://searxng:8080/search?q=...&format=json`, per `SEARXNG_URL`) —
never the public internet directly, and never a caller-supplied
destination (unlike `/fetch`'s own `url` query param, which the agent DOES
control). SearXNG itself lives on `homeai-internal` only
(docker-compose.yml) and reaches the actual public search engines through
`egress-proxy`, the same egress chokepoint `/fetch` uses — this endpoint
never talks to the public internet itself, directly or otherwise.
"""

from __future__ import annotations

import json
import logging

import httpx
from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.core.config import Settings

logger = logging.getLogger("web_fetch.search")

router = APIRouter()

MIN_RESULTS = 1
MAX_RESULTS = 20
DEFAULT_RESULTS = 8


class SearchResult(BaseModel):
    title: str
    url: str
    snippet: str
    engine: str


class SearchResponse(BaseModel):
    query: str
    results: list[SearchResult]


def _error(status_code: int, error: str) -> JSONResponse:
    return JSONResponse(status_code=status_code, content={"error": error})


def _settings(request: Request) -> Settings:
    return request.app.state.settings


@router.get("/search", response_model=SearchResponse)
async def search(
    request: Request,
    q: str,
    n: int = Query(default=DEFAULT_RESULTS, ge=MIN_RESULTS, le=MAX_RESULTS),
) -> JSONResponse:
    settings = _settings(request)

    try:
        async with httpx.AsyncClient(timeout=settings.fetch_timeout_s) as client:
            response = await client.get(
                f"{settings.searxng_url}/search",
                params={"q": q, "format": "json"},
            )
    except httpx.HTTPError as exc:
        # SearXNG unreachable, connection reset, timed out, etc. (spec §3:
        # "SearXNG unreachable/erroring -> 502").
        return _error(502, f"searxng request failed: {exc!r}")

    if response.status_code != 200:
        return _error(502, f"searxng returned HTTP {response.status_code}")

    try:
        payload = response.json()
    except ValueError as exc:
        return _error(502, f"searxng returned invalid JSON: {exc}")

    raw_results = payload.get("results")
    if not isinstance(raw_results, list):
        return _error(502, "searxng response missing a 'results' list")

    # De-duplicate by URL (spec §3) - SearXNG can return the same URL from
    # more than one of the enabled engines - and cap at `n`, preserving
    # SearXNG's own relevance ordering (`get_ordered_results()` upstream)
    # rather than re-sorting.
    seen_urls: set[str] = set()
    results: list[SearchResult] = []
    for item in raw_results:
        if not isinstance(item, dict):
            continue
        url = item.get("url")
        if not url or url in seen_urls:
            continue
        seen_urls.add(url)
        results.append(
            SearchResult(
                title=item.get("title") or "",
                url=url,
                snippet=item.get("content") or "",
                engine=item.get("engine") or "",
            )
        )
        if len(results) >= n:
            break

    result = SearchResponse(query=q, results=results)
    return JSONResponse(status_code=200, content=json.loads(result.model_dump_json()))

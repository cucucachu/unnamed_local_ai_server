"""`GET /search` HTTP-level tests (M7-04), against `httpx.AsyncClient`
mocked via `respx` — no real SearXNG needed, same technique as
`test_fetch.py`'s own SearXNG-less `/fetch` tests.
"""

from __future__ import annotations

import httpx
import respx
from httpx import AsyncClient

SEARXNG_SEARCH_URL = "http://searxng:8080/search"


def _searxng_json(results: list[dict]) -> dict:
    return {
        "query": "llama.cpp",
        "number_of_results": len(results),
        "results": results,
        "answers": [],
        "corrections": [],
        "infoboxes": [],
        "suggestions": [],
        "unresponsive_engines": [],
    }


@respx.mock
async def test_search_happy_path(client: AsyncClient) -> None:
    respx.get(SEARXNG_SEARCH_URL).mock(
        return_value=httpx.Response(
            200,
            json=_searxng_json(
                [
                    {
                        "title": "llama.cpp",
                        "url": "https://github.com/ggml-org/llama.cpp",
                        "content": "LLM inference in C/C++",
                        "engine": "github",
                    },
                    {
                        "title": "llama.cpp - Wikipedia",
                        "url": "https://en.wikipedia.org/wiki/Llama.cpp",
                        "content": "llama.cpp is a library for LLM inference",
                        "engine": "wikipedia",
                    },
                ]
            ),
        )
    )

    response = await client.get("/search", params={"q": "llama.cpp"})

    assert response.status_code == 200
    body = response.json()
    assert body["query"] == "llama.cpp"
    assert len(body["results"]) == 2
    assert body["results"][0] == {
        "title": "llama.cpp",
        "url": "https://github.com/ggml-org/llama.cpp",
        "snippet": "LLM inference in C/C++",
        "engine": "github",
    }


@respx.mock
async def test_search_dedupes_by_url(client: AsyncClient) -> None:
    respx.get(SEARXNG_SEARCH_URL).mock(
        return_value=httpx.Response(
            200,
            json=_searxng_json(
                [
                    {
                        "title": "First",
                        "url": "https://example.com/same",
                        "content": "from engine a",
                        "engine": "brave",
                    },
                    {
                        "title": "Duplicate",
                        "url": "https://example.com/same",
                        "content": "from engine b",
                        "engine": "mojeek",
                    },
                    {
                        "title": "Second",
                        "url": "https://example.com/other",
                        "content": "distinct",
                        "engine": "brave",
                    },
                ]
            ),
        )
    )

    response = await client.get("/search", params={"q": "dedupe test"})

    assert response.status_code == 200
    body = response.json()
    urls = [r["url"] for r in body["results"]]
    assert urls == ["https://example.com/same", "https://example.com/other"]


@respx.mock
async def test_search_caps_at_n(client: AsyncClient) -> None:
    raw_results = [
        {
            "title": f"Result {i}",
            "url": f"https://example.com/{i}",
            "content": "content",
            "engine": "brave",
        }
        for i in range(10)
    ]
    respx.get(SEARXNG_SEARCH_URL).mock(return_value=httpx.Response(200, json=_searxng_json(raw_results)))

    response = await client.get("/search", params={"q": "cap test", "n": 3})

    assert response.status_code == 200
    assert len(response.json()["results"]) == 3


async def test_search_n_defaults_to_eight(client: AsyncClient) -> None:
    raw_results = [
        {
            "title": f"Result {i}",
            "url": f"https://example.com/{i}",
            "content": "content",
            "engine": "brave",
        }
        for i in range(10)
    ]
    with respx.mock:
        respx.get(SEARXNG_SEARCH_URL).mock(return_value=httpx.Response(200, json=_searxng_json(raw_results)))
        response = await client.get("/search", params={"q": "default cap"})

    assert response.status_code == 200
    assert len(response.json()["results"]) == 8


async def test_search_n_out_of_range_returns_422(client: AsyncClient) -> None:
    response = await client.get("/search", params={"q": "too many", "n": 21})
    assert response.status_code == 422

    response = await client.get("/search", params={"q": "too few", "n": 0})
    assert response.status_code == 422


@respx.mock
async def test_search_searxng_5xx_returns_502(client: AsyncClient) -> None:
    respx.get(SEARXNG_SEARCH_URL).mock(return_value=httpx.Response(500, text="internal error"))

    response = await client.get("/search", params={"q": "broken"})

    assert response.status_code == 502
    assert "error" in response.json()


@respx.mock
async def test_search_searxng_unreachable_returns_502(client: AsyncClient) -> None:
    respx.get(SEARXNG_SEARCH_URL).mock(side_effect=httpx.ConnectError("connection refused"))

    response = await client.get("/search", params={"q": "unreachable"})

    assert response.status_code == 502
    assert "error" in response.json()


@respx.mock
async def test_search_searxng_invalid_json_returns_502(client: AsyncClient) -> None:
    respx.get(SEARXNG_SEARCH_URL).mock(
        return_value=httpx.Response(200, text="not json", headers={"content-type": "text/plain"})
    )

    response = await client.get("/search", params={"q": "bad json"})

    assert response.status_code == 502
    assert "error" in response.json()


@respx.mock
async def test_search_empty_results(client: AsyncClient) -> None:
    respx.get(SEARXNG_SEARCH_URL).mock(return_value=httpx.Response(200, json=_searxng_json([])))

    response = await client.get("/search", params={"q": "nothing found"})

    assert response.status_code == 200
    assert response.json()["results"] == []

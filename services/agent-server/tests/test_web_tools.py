"""Unit tests for `app.agent.web_tools.make_web_search_tool`/`make_web_fetch_tool`.

Exercises both tools directly (`.ainvoke(...)`, not through the full agent
graph) against a `respx`-mocked `web-fetch` — mirrors
`test_execute_code_tool.py`'s "unit tests against a fake HTTP dependency,
agent-level coverage lives in `test_chat_ws.py`" split, just with `respx`
route-mocking instead of a real fake-server fixture (web-fetch's `/fetch`
and `/search` contracts are simple enough JSON in/out that a real bound
server isn't needed here the way `fake_exec_manager`'s stateful
ensure/execute session lifecycle was).
"""

import httpx
import respx

from app.agent.web_tools import make_web_fetch_tool, make_web_search_tool
from app.core.config import Settings

WEB_FETCH_BASE_URL = "http://web-fetch.test:8000"


def _settings(**overrides: object) -> Settings:
    overrides.setdefault("web_fetch_url", WEB_FETCH_BASE_URL)
    overrides.setdefault("_env_file", None)
    return Settings(**overrides)


# --- web_search ---------------------------------------------------------


@respx.mock
async def test_web_search_happy_path_exact_formatting() -> None:
    respx.get(f"{WEB_FETCH_BASE_URL}/search").mock(
        return_value=httpx.Response(
            200,
            json={
                "query": "llama.cpp",
                "results": [
                    {
                        "title": "ggml-org/llama.cpp",
                        "url": "https://github.com/ggml-org/llama.cpp",
                        "snippet": "LLM inference in C/C++",
                        "engine": "github",
                    },
                    {
                        "title": "llama.cpp docs",
                        "url": "https://example.com/docs",
                        "snippet": "Documentation site",
                        "engine": "duckduckgo",
                    },
                ],
            },
        )
    )
    tool = make_web_search_tool(_settings())

    result = await tool.ainvoke({"query": "llama.cpp"})

    assert result == (
        "1. ggml-org/llama.cpp\n"
        "   https://github.com/ggml-org/llama.cpp\n"
        "   LLM inference in C/C++\n"
        "2. llama.cpp docs\n"
        "   https://example.com/docs\n"
        "   Documentation site"
    )


@respx.mock
async def test_web_search_sends_query_and_clamped_max_results() -> None:
    route = respx.get(f"{WEB_FETCH_BASE_URL}/search").mock(
        return_value=httpx.Response(200, json={"query": "x", "results": []})
    )
    tool = make_web_search_tool(_settings())

    await tool.ainvoke({"query": "x", "max_results": 999})

    assert route.calls.last.request.url.params["q"] == "x"
    assert route.calls.last.request.url.params["n"] == "20"


@respx.mock
async def test_web_search_no_results() -> None:
    respx.get(f"{WEB_FETCH_BASE_URL}/search").mock(
        return_value=httpx.Response(200, json={"query": "x", "results": []})
    )
    tool = make_web_search_tool(_settings())

    result = await tool.ainvoke({"query": "x"})

    assert result == "No results found."


@respx.mock
async def test_web_search_502_returns_error_text_not_exception() -> None:
    respx.get(f"{WEB_FETCH_BASE_URL}/search").mock(
        return_value=httpx.Response(502, json={"error": "searxng returned HTTP 500"})
    )
    tool = make_web_search_tool(_settings())

    result = await tool.ainvoke({"query": "x"})

    assert result == "Error: searxng returned HTTP 500"


@respx.mock
async def test_web_search_unreachable_returns_error_text_not_exception() -> None:
    respx.get(f"{WEB_FETCH_BASE_URL}/search").mock(side_effect=httpx.ConnectError("boom"))
    tool = make_web_search_tool(_settings())

    result = await tool.ainvoke({"query": "x"})

    assert result.startswith("Error: web_search failed: ")


# --- web_fetch -----------------------------------------------------------


@respx.mock
async def test_web_fetch_happy_path_exact_formatting() -> None:
    respx.get(f"{WEB_FETCH_BASE_URL}/fetch").mock(
        return_value=httpx.Response(
            200,
            json={
                "url": "http://example.com/",
                "final_url": "https://example.com/",
                "title": "Example Domain",
                "content_type": "text/html",
                "text": "This is an example page.",
                "truncated": False,
                "fetched_at": "2026-09-04T00:00:00+00:00",
            },
        )
    )
    tool = make_web_fetch_tool(_settings())

    result = await tool.ainvoke({"url": "http://example.com/"})

    assert result == (
        "Title: Example Domain\n"
        "URL: https://example.com/\n"
        "\n"
        "This is an example page."
    )


@respx.mock
async def test_web_fetch_sends_url_param() -> None:
    route = respx.get(f"{WEB_FETCH_BASE_URL}/fetch").mock(
        return_value=httpx.Response(
            200,
            json={
                "url": "http://example.com/x",
                "final_url": "http://example.com/x",
                "title": None,
                "content_type": "text/plain",
                "text": "hi",
                "truncated": False,
                "fetched_at": "2026-09-04T00:00:00+00:00",
            },
        )
    )
    tool = make_web_fetch_tool(_settings())

    await tool.ainvoke({"url": "http://example.com/x"})

    assert route.calls.last.request.url.params["url"] == "http://example.com/x"


@respx.mock
async def test_web_fetch_missing_title_falls_back_to_untitled() -> None:
    respx.get(f"{WEB_FETCH_BASE_URL}/fetch").mock(
        return_value=httpx.Response(
            200,
            json={
                "url": "http://example.com/x",
                "final_url": "http://example.com/x",
                "title": None,
                "content_type": "text/plain",
                "text": "hi",
                "truncated": False,
                "fetched_at": "2026-09-04T00:00:00+00:00",
            },
        )
    )
    tool = make_web_fetch_tool(_settings())

    result = await tool.ainvoke({"url": "http://example.com/x"})

    assert result == "Title: (untitled)\nURL: http://example.com/x\n\nhi"


@respx.mock
async def test_web_fetch_truncates_at_tool_side_cap() -> None:
    long_text = "a" * 100
    respx.get(f"{WEB_FETCH_BASE_URL}/fetch").mock(
        return_value=httpx.Response(
            200,
            json={
                "url": "http://example.com/x",
                "final_url": "http://example.com/x",
                "title": "Long Page",
                "content_type": "text/plain",
                "text": long_text,
                "truncated": False,
                "fetched_at": "2026-09-04T00:00:00+00:00",
            },
        )
    )
    tool = make_web_fetch_tool(_settings(web_fetch_tool_max_chars=10))

    result = await tool.ainvoke({"url": "http://example.com/x"})

    assert result == "Title: Long Page\nURL: http://example.com/x\n\n" + ("a" * 10) + "\n[content truncated]"


@respx.mock
async def test_web_fetch_502_proxy_denial_returns_error_text_not_exception() -> None:
    """Spec point 3: a proxy 403 (surfaced by web-fetch as a 502 passthrough)
    reads e.g. `Error: destination not allowed by egress policy` so the model
    learns the boundary — never raised."""
    respx.get(f"{WEB_FETCH_BASE_URL}/fetch").mock(
        return_value=httpx.Response(
            502,
            json={"error": "destination not allowed by egress policy", "upstream_status": 403},
        )
    )
    tool = make_web_fetch_tool(_settings())

    result = await tool.ainvoke({"url": "http://169.254.169.254/"})

    assert result == "Error: destination not allowed by egress policy"


@respx.mock
async def test_web_fetch_unreachable_returns_error_text_not_exception() -> None:
    respx.get(f"{WEB_FETCH_BASE_URL}/fetch").mock(side_effect=httpx.ConnectError("boom"))
    tool = make_web_fetch_tool(_settings())

    result = await tool.ainvoke({"url": "http://example.com/"})

    assert result.startswith("Error: web_fetch failed: ")


@respx.mock
async def test_web_fetch_non_json_error_body_falls_back_to_text() -> None:
    respx.get(f"{WEB_FETCH_BASE_URL}/fetch").mock(
        return_value=httpx.Response(504, text="Gateway Timeout")
    )
    tool = make_web_fetch_tool(_settings())

    result = await tool.ainvoke({"url": "http://example.com/"})

    assert result == "Error: Gateway Timeout"

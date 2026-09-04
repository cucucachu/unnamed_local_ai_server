"""`web_search` / `web_fetch`: the agent's read-only window onto the public web.

Thin HTTP clients against `web-fetch` (`app.core.config.Settings.web_fetch_url`)
— same shape as `app/agent/execute_code_tool.py`'s pattern: a `make_*_tool(settings)`
factory closing over one `Settings` instance (so tests can point `web_fetch_url`
at a fake server per test, same reasoning as that module's own docstring),
returning a `@tool`-decorated async function that talks HTTP and formats a
plain-text result.

Neither tool ever raises. Every failure — a non-2xx from `web-fetch` (which
includes its own passthrough of `egress-proxy`'s method/destination-guard
403s, e.g. `{"error": "destination not allowed by egress policy"}`, spec
point 3) and any transport-level error (unreachable, timed out, DNS
failure, ...) — is returned as an `"Error: ..."` string instead of raised.
This deliberately matches deepagents' own built-in filesystem tools
(`deepagents/middleware/filesystem.py`), which catch their own errors and
return a normal (`status="success"`) `ToolMessage` whose content starts with
`"Error: ..."` rather than firing `on_tool_error` — see `app/api/chat_ws.py`'s
module doc for why that distinction matters here: an `on_tool_error` event
propagates past the whole graph run and aborts the turn (`error` frame,
close 1011), which would defeat the entire point of a tool the model is
meant to try, fail, and adapt to (e.g. learning a URL is blocked and trying
a different one) rather than blowing up the conversation.
"""

from __future__ import annotations

import httpx
from langchain_core.tools import tool

from app.core.config import Settings

_SEARCH_MIN_RESULTS = 1
_SEARCH_MAX_RESULTS = 20
_SEARCH_DEFAULT_RESULTS = 8

# Generous relative to `web-fetch`'s own server-side timeouts
# (`FETCH_TIMEOUT_S`, default 20s, governs both its `/fetch` and — via the
# same `Settings.fetch_timeout_s` — its `/search` call to SearXNG): this
# client must never time out *before* `web-fetch` itself would, or its own
# real (potentially useful, e.g. a 504) response would never be seen.
_HTTP_CLIENT_TIMEOUT_S = 30.0


def _clamp_max_results(max_results: int) -> int:
    return max(_SEARCH_MIN_RESULTS, min(_SEARCH_MAX_RESULTS, max_results))


def _error_message(response: httpx.Response) -> str:
    """Extract the human-readable message from a non-2xx `web-fetch` response.

    Every documented `web-fetch` error response is `{"error": str, ...}`
    (`services/web-fetch/app/api/fetch.py` / `.../search.py`'s `_error`
    helper) — but this client shouldn't assume that shape holds for
    something unexpected answering instead (a stray proxy/gateway HTML
    error page, an empty body, ...), so it falls back to the raw response
    text, then the HTTP reason phrase, rather than raising a JSON-decode
    error of its own.
    """
    try:
        payload = response.json()
    except ValueError:
        return response.text.strip() or response.reason_phrase
    if isinstance(payload, dict) and isinstance(payload.get("error"), str):
        return payload["error"]
    return response.text.strip() or response.reason_phrase


def _format_search_results(results: list[dict]) -> str:
    if not results:
        return "No results found."
    lines = []
    for index, result in enumerate(results, start=1):
        title = result.get("title") or "(untitled)"
        url = result.get("url") or ""
        snippet = result.get("snippet") or ""
        lines.append(f"{index}. {title}\n   {url}\n   {snippet}")
    return "\n".join(lines)


def make_web_search_tool(settings: Settings):
    """Build the `web_search` tool bound to one `Settings` instance.

    Factory rather than a single module-level tool, for the same reason
    `make_execute_code_tool` is (see its own docstring): `build_agent`
    receives a distinct `Settings` per call, and closing over it here is
    the only way to make it available to a fixed-signature `@tool`
    function.
    """

    @tool
    async def web_search(query: str, max_results: int = 8) -> str:
        """Search the public web via the internal SearXNG-backed search service.

        Returns a numbered list of results (title, URL, snippet) — use web_fetch on
        the most promising URL(s) before citing anything, since a snippet alone is
        rarely enough to answer accurately. max_results is capped at 20.
        """
        n = _clamp_max_results(max_results)
        try:
            async with httpx.AsyncClient(
                base_url=settings.web_fetch_url, timeout=_HTTP_CLIENT_TIMEOUT_S
            ) as client:
                response = await client.get("/search", params={"q": query, "n": n})
        except httpx.HTTPError as exc:
            return f"Error: web_search failed: {exc!r}"

        if response.status_code != 200:
            return f"Error: {_error_message(response)}"

        payload = response.json()
        results = payload.get("results") or []
        return _format_search_results(results)

    return web_search


def make_web_fetch_tool(settings: Settings):
    """Build the `web_fetch` tool bound to one `Settings` instance."""

    @tool
    async def web_fetch(url: str) -> str:
        """Fetch a URL and return its readable title + text content.

        Follows redirects; supports HTML, plain text, markdown, CSV, JSON, and PDF.
        Long pages are truncated. Use this to read a page after web_search finds it,
        or to fetch a URL the user gave you directly.
        """
        try:
            async with httpx.AsyncClient(
                base_url=settings.web_fetch_url, timeout=_HTTP_CLIENT_TIMEOUT_S
            ) as client:
                response = await client.get("/fetch", params={"url": url})
        except httpx.HTTPError as exc:
            return f"Error: web_fetch failed: {exc!r}"

        if response.status_code != 200:
            return f"Error: {_error_message(response)}"

        payload = response.json()
        title = payload.get("title") or "(untitled)"
        final_url = payload.get("final_url") or url
        text = payload.get("text") or ""

        if len(text) > settings.web_fetch_tool_max_chars:
            text = text[: settings.web_fetch_tool_max_chars] + "\n[content truncated]"

        return f"Title: {title}\nURL: {final_url}\n\n{text}"

    return web_fetch

"""`search_files`: the user's text files by meaning and by words (M21-04, docs/MEMORY.md §5).

The platform owns the index and decides what this user may search
(`POST /api/platform/files/search`); like the file tools, the call carries
the run's delegation (`configurable["delegation"]`), and no delegation
means no call. Failures are results the model reads, never a failed turn.
"""

from __future__ import annotations

import httpx
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool, tool

from app.core.config import Settings

SEARCH_TOOL_NAME = "search_files"
MAX_LIMIT = 50
_HTTP_TIMEOUT_S = 30.0
_WORDS_ONLY = (
    "(Matched by words only: searching by meaning is unavailable right now, "
    "so try the exact words you expect in the file.)"
)


def format_results(query: str, path: str, body: dict) -> str:
    results = body.get("results") or []
    note = "" if body.get("semantic", True) else f"\n{_WORDS_ONLY}"
    if not results:
        return f'No matches for "{query}" under {path}.{note}'
    count = f"{len(results)} match" if len(results) == 1 else f"{len(results)} matches"
    lines = [f'{count} for "{query}" under {path}, best first:']
    for hit in results:
        lines.append(f"\n{hit['path']} (lines {hit['line_start']}-{hit['line_end']})")
        lines.extend(f"  {line}" for line in str(hit.get("snippet", "")).splitlines())
    return "\n".join(lines) + note


def _error(status: int, detail: str | None, path: str) -> str:
    if status == 404:
        return f"Error: {path} not found (paths start with /personal/ or /spaces/<slug>/)."
    if status == 401:
        return "Error: file search is unavailable for this run (no delegation)."
    if status == 422 and detail:
        return f"Error: file search refused the request: {detail}."
    return f"Error: file search failed (HTTP {status}). Try grep instead."


def make_search_files_tool(settings: Settings) -> BaseTool:
    @tool
    async def search_files(
        query: str, config: RunnableConfig, path: str = "/", limit: int = 10
    ) -> str:
        """Search the user's text files (notes, documents, app sources) by meaning and by words.

        Finds passages worded differently from the query, as well as exact names and words.
        `path` narrows the search to a folder or a file (/personal/..., /spaces/<slug>/...);
        "/" searches everything the user can read. Returns up to `limit` (at most 50) matches,
        best first, each with its path, line range and a short snippet: read_file the path for
        more. Recent changes can take a moment to show up. To find every occurrence of an exact
        string, use grep instead.
        """
        token = getattr((config.get("configurable") or {}).get("delegation"), "token", None)
        if not isinstance(token, str) or not token:
            return _error(401, None, path)
        path = "/" + path.strip().lstrip("/") if path.strip() else "/"
        body = {"query": query, "path": path, "limit": max(1, min(MAX_LIMIT, limit))}
        try:
            async with httpx.AsyncClient(
                base_url=settings.platform_url, timeout=_HTTP_TIMEOUT_S
            ) as client:
                response = await client.post(
                    "/api/platform/files/search",
                    json=body,
                    headers={"Authorization": f"Bearer {token}"},
                )
        except httpx.HTTPError as exc:
            return f"Error: file search is unreachable right now ({type(exc).__name__}). Try grep."
        if response.status_code != 200:
            try:
                detail = response.json().get("detail")
            except ValueError:
                detail = None
            return _error(response.status_code, detail if isinstance(detail, str) else None, path)
        return format_results(query, path, response.json())

    return search_files

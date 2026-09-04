"""`GET /fetch` (spec §2) — the only real endpoint this service exposes.

Turns a URL into readable text: validates the scheme, makes the request
through `egress-proxy` (never talks to the public internet directly — see
`docs/ARCHITECTURE.md` §5), enforces the byte/time/redirect caps, and
extracts text via `app.core.extract`. The agent (via `agent-server`, once
M7-05 wires up a tool) never sees raw HTML and never holds a network handle
itself — this service is the only thing that does.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from urllib.parse import urlsplit

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.core.config import Settings
from app.core.extract import SUPPORTED_CONTENT_TYPES, base_content_type, extract

logger = logging.getLogger("web_fetch.fetch")

router = APIRouter()

ALLOWED_SCHEMES = frozenset({"http", "https"})

USER_AGENT = "HomeAI-Agent/1.0 (+read-only)"

# Read the response body in fixed-size chunks while enforcing
# `FETCH_MAX_BYTES` (spec §2) rather than reading it all at once — a
# malicious/misbehaving upstream advertising a small `Content-Length` but
# then streaming forever must still be caught, not just a large declared
# `Content-Length` (the resolution `egress-proxy` itself already enforces
# for its own, much larger `EGRESS_MAX_BYTES` cap).
_CHUNK_SIZE = 64 * 1024

# Upper bound on how much of an error-response body (a non-2xx from the
# origin or `egress-proxy`'s own synthesized 403) this service will read
# before giving up on getting the rest — error bodies are normally tiny
# (`egress-proxy`'s policy.py 403s are a one-line JSON blob), but this
# still bounds memory use against a pathological upstream 500 page.
_ERROR_BODY_MAX_BYTES = 64 * 1024


class FetchResponse(BaseModel):
    url: str
    final_url: str
    title: str | None
    content_type: str
    text: str
    truncated: bool
    fetched_at: str


def _error(status_code: int, error: str, **extra: object) -> JSONResponse:
    return JSONResponse(status_code=status_code, content={"error": error, **extra})


def _settings(request: Request) -> Settings:
    return request.app.state.settings


async def _read_capped(response: httpx.Response, max_bytes: int) -> tuple[bytes, bool]:
    """Read `response`'s body up to `max_bytes`, returning `(body,
    exceeded)`. Streams in `_CHUNK_SIZE` chunks rather than trusting
    `Content-Length` (see `_CHUNK_SIZE`'s own comment)."""
    chunks: list[bytes] = []
    total = 0
    async for chunk in response.aiter_bytes(_CHUNK_SIZE):
        total += len(chunk)
        if total > max_bytes:
            return b"".join(chunks), True
        chunks.append(chunk)
    return b"".join(chunks), False


@router.get("/fetch", response_model=FetchResponse)
async def fetch(url: str, request: Request) -> JSONResponse:
    settings = _settings(request)

    scheme = urlsplit(url).scheme.lower()
    if scheme not in ALLOWED_SCHEMES:
        return _error(400, f"unsupported URL scheme {scheme!r}; only http/https are allowed")

    try:
        async with httpx.AsyncClient(
            proxy=settings.egress_proxy_url,
            follow_redirects=True,
            max_redirects=settings.fetch_max_redirects,
            timeout=settings.fetch_timeout_s,
            headers={"User-Agent": USER_AGENT},
        ) as client, client.stream("GET", url) as response:
            if response.status_code >= 400:
                # Covers both a real upstream 4xx/5xx AND egress-proxy's
                # own synthesized 403 (method/destination guard) - the
                # body text is passed through either way so the agent
                # learns *why* (spec §2), e.g. policy.py's
                # `{"error": "destination not allowed by egress
                # policy"}`.
                body, _ = await _read_capped(response, _ERROR_BODY_MAX_BYTES)
                message = body.decode("utf-8", errors="replace").strip() or response.reason_phrase
                return _error(502, message, upstream_status=response.status_code)

            content_type = base_content_type(response.headers.get("content-type"))
            if content_type not in SUPPORTED_CONTENT_TYPES:
                return _error(415, f"unsupported content type {content_type!r}")

            body, exceeded = await _read_capped(response, settings.fetch_max_bytes)
            if exceeded:
                return _error(
                    413, f"response exceeded FETCH_MAX_BYTES ({settings.fetch_max_bytes})"
                )

            final_url = str(response.url)
    except httpx.TimeoutException:
        return _error(504, f"upstream request timed out after {settings.fetch_timeout_s}s")
    except httpx.TooManyRedirects:
        return _error(502, f"exceeded FETCH_MAX_REDIRECTS ({settings.fetch_max_redirects})")
    except httpx.HTTPError as exc:
        # Anything else httpx can raise (connection reset, proxy refused the
        # connection, DNS failure surfaced as a transport error, etc.) - not
        # a case the spec calls out by name, so treated the same as any
        # other "couldn't reach the destination" outcome.
        return _error(502, f"fetch failed: {exc!r}")

    try:
        text, title = extract(content_type, body)
    except (ValueError, json.JSONDecodeError) as exc:
        # A body that claims a supported Content-Type but doesn't actually
        # parse as one (e.g. `application/json` with invalid JSON) - not a
        # network/policy failure, so 502 (matching "upstream sent something
        # broken") rather than a 4xx that would imply the caller's own
        # request was malformed.
        return _error(502, f"failed to extract content: {exc}")

    truncated = len(text) > settings.fetch_max_text_chars
    if truncated:
        text = text[: settings.fetch_max_text_chars]

    result = FetchResponse(
        url=url,
        final_url=final_url,
        title=title,
        content_type=content_type,
        text=text,
        truncated=truncated,
        fetched_at=datetime.now(UTC).isoformat(),
    )
    return JSONResponse(status_code=200, content=json.loads(result.model_dump_json()))

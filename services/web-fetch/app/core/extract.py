"""Content-type-aware "raw bytes -> readable text" extraction (spec §2).

Every function here is pure (bytes/str in, str/tuple out) — no network, no
FastAPI — so it's unit-testable directly against small fixtures, independent
of the `/fetch` endpoint's HTTP plumbing (`app/api/fetch.py`).
"""

from __future__ import annotations

import json
import logging
from io import BytesIO

import markdownify
import trafilatura
from pypdf import PdfReader
from readability import Document

logger = logging.getLogger("web_fetch.extract")

# Base content types (no `; charset=...` suffix) this service knows how to
# turn into text. Anything else -> 415 (see `app/api/fetch.py`).
SUPPORTED_CONTENT_TYPES = frozenset(
    {
        "text/html",
        "text/plain",
        "text/markdown",
        "text/csv",
        "application/json",
        "application/pdf",
    }
)

# pypdf pages are 0-indexed; this is a count, not an index.
PDF_MAX_PAGES = 50


def base_content_type(header_value: str | None) -> str:
    """Strip `; charset=...`/other parameters and lower-case, e.g.
    `"text/html; charset=utf-8"` -> `"text/html"`. Empty/`None` -> `""`
    (never matches `SUPPORTED_CONTENT_TYPES`, so it 415s like any other
    unsupported type rather than crashing)."""
    if not header_value:
        return ""
    return header_value.split(";", 1)[0].strip().lower()


def _decode(body: bytes) -> str:
    """Best-effort UTF-8 decode for text-ish bodies — a public web page with
    an undeclared/wrong charset is much more useful to the agent as slightly
    mangled text than as a hard failure."""
    return body.decode("utf-8", errors="replace")


def _extract_title(html_text: str) -> str | None:
    """`readability.Document.title()` rather than a hand-rolled `<title>`
    regex/lxml walk — `readability-lxml` is already a required dependency
    for the HTML fallback path below, and its title extraction already
    handles the malformed-HTML cases a naive regex wouldn't (unclosed tags,
    multiple `<title>` elements, etc.)."""
    try:
        title = Document(html_text).title()
    except Exception:  # noqa: BLE001 - malformed HTML must never 500 the request
        return None
    if not title or title == "[no-title]":  # readability's own sentinel
        return None
    return title


def _extract_html(html_text: str) -> tuple[str, str | None]:
    """`trafilatura` first (spec §2: `output_format="markdown",
    include_links=True, include_tables=True`); fall back to
    `readability-lxml` + `markdownify` when trafilatura returns nothing
    (e.g. a page too short/unstructured for trafilatura's boilerplate
    heuristics to recognize a main-content region at all — trafilatura
    returns `None` in that case rather than raising)."""
    title = _extract_title(html_text)

    text = trafilatura.extract(
        html_text, output_format="markdown", include_links=True, include_tables=True
    )
    if text:
        return text, title

    logger.info("trafilatura returned no content, falling back to readability+markdownify")
    try:
        summary_html = Document(html_text).summary()
    except Exception:  # noqa: BLE001 - fall through to "no content" below
        summary_html = ""
    text = markdownify.markdownify(summary_html, heading_style="ATX").strip() if summary_html else ""
    return text, title


def _extract_pdf(body: bytes) -> str:
    reader = PdfReader(BytesIO(body))
    pages = reader.pages[:PDF_MAX_PAGES]
    parts = [page.extract_text() or "" for page in pages]
    return "\n\n".join(part for part in parts if part)


def extract(content_type: str, body: bytes) -> tuple[str, str | None]:
    """`(text, title)` for `content_type` (already the base type, i.e.
    already passed through `base_content_type`) and raw response `body`.

    `title` is always `None` for non-HTML content types. Caller
    (`app/api/fetch.py`) is responsible for the `SUPPORTED_CONTENT_TYPES`
    check (415) before calling this — this function assumes `content_type`
    is one of them.
    """
    if content_type == "text/html":
        return _extract_html(_decode(body))
    if content_type in ("text/plain", "text/markdown", "text/csv"):
        return _decode(body), None
    if content_type == "application/json":
        parsed = json.loads(_decode(body))
        return json.dumps(parsed, indent=2, ensure_ascii=False), None
    if content_type == "application/pdf":
        return _extract_pdf(body), None
    raise ValueError(f"unsupported content type: {content_type!r}")  # pragma: no cover - guarded by caller

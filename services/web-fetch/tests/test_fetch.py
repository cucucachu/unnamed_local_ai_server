"""`GET /fetch` HTTP-level tests, against `httpx.AsyncClient` mocked via
`respx` — no real proxy or real internet needed (`respx` intercepts at the
transport level regardless of the `proxy=` argument the app's own client
passes, confirmed against a real `AsyncClient(proxy=...)` before writing
these tests).
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import respx
from httpx import AsyncClient

FIXTURES = Path(__file__).parent / "fixtures"


@respx.mock
async def test_html_fetch_extracts_text_and_title(client: AsyncClient) -> None:
    html = (FIXTURES / "sample.html").read_text()
    respx.get("http://example.com/").mock(
        return_value=httpx.Response(200, text=html, headers={"content-type": "text/html; charset=utf-8"})
    )

    response = await client.get("/fetch", params={"url": "http://example.com/"})

    assert response.status_code == 200
    body = response.json()
    assert body["url"] == "http://example.com/"
    assert body["final_url"] == "http://example.com/"
    assert body["title"] == "Sample Article Title"
    assert body["content_type"] == "text/html"
    assert "Sample Article Title" in body["text"]
    assert body["truncated"] is False
    assert "fetched_at" in body


@respx.mock
async def test_plain_text_passthrough(client: AsyncClient) -> None:
    respx.get("http://example.com/robots.txt").mock(
        return_value=httpx.Response(200, text="User-agent: *\n", headers={"content-type": "text/plain"})
    )

    response = await client.get("/fetch", params={"url": "http://example.com/robots.txt"})

    assert response.status_code == 200
    assert response.json()["text"] == "User-agent: *\n"
    assert response.json()["title"] is None


@respx.mock
async def test_markdown_passthrough(client: AsyncClient) -> None:
    respx.get("http://example.com/readme").mock(
        return_value=httpx.Response(200, text="# Title\n", headers={"content-type": "text/markdown"})
    )
    response = await client.get("/fetch", params={"url": "http://example.com/readme"})
    assert response.status_code == 200
    assert response.json()["content_type"] == "text/markdown"


@respx.mock
async def test_csv_passthrough(client: AsyncClient) -> None:
    respx.get("http://example.com/data.csv").mock(
        return_value=httpx.Response(200, text="a,b\n1,2\n", headers={"content-type": "text/csv"})
    )
    response = await client.get("/fetch", params={"url": "http://example.com/data.csv"})
    assert response.status_code == 200
    assert response.json()["text"] == "a,b\n1,2\n"


@respx.mock
async def test_json_pretty_printed(client: AsyncClient) -> None:
    respx.get("http://example.com/api").mock(
        return_value=httpx.Response(
            200, json={"b": 2, "a": 1}, headers={"content-type": "application/json"}
        )
    )
    response = await client.get("/fetch", params={"url": "http://example.com/api"})
    assert response.status_code == 200
    assert response.json()["text"] == json.dumps({"b": 2, "a": 1}, indent=2, ensure_ascii=False)


@respx.mock
async def test_pdf_extraction(client: AsyncClient) -> None:
    pdf_bytes = (FIXTURES / "sample.pdf").read_bytes()
    respx.get("http://example.com/doc.pdf").mock(
        return_value=httpx.Response(200, content=pdf_bytes, headers={"content-type": "application/pdf"})
    )
    response = await client.get("/fetch", params={"url": "http://example.com/doc.pdf"})
    assert response.status_code == 200
    assert "Hello PDF fixture text" in response.json()["text"]


@respx.mock
async def test_unsupported_content_type_returns_415(client: AsyncClient) -> None:
    respx.get("http://example.com/binary").mock(
        return_value=httpx.Response(200, content=b"\x00\x01", headers={"content-type": "application/zip"})
    )
    response = await client.get("/fetch", params={"url": "http://example.com/binary"})
    assert response.status_code == 415
    assert "error" in response.json()


@respx.mock
async def test_oversized_response_returns_413(client: AsyncClient) -> None:
    # test_settings fixture sets fetch_max_bytes=1_000_000
    big_body = b"x" * 1_000_001
    respx.get("http://example.com/big").mock(
        return_value=httpx.Response(200, content=big_body, headers={"content-type": "text/plain"})
    )
    response = await client.get("/fetch", params={"url": "http://example.com/big"})
    assert response.status_code == 413
    assert "error" in response.json()


@respx.mock
async def test_text_truncated_at_fetch_max_text_chars(client: AsyncClient) -> None:
    long_text = "a" * 50_000  # test_settings fixture sets fetch_max_text_chars=40_000
    respx.get("http://example.com/long").mock(
        return_value=httpx.Response(200, text=long_text, headers={"content-type": "text/plain"})
    )
    response = await client.get("/fetch", params={"url": "http://example.com/long"})
    assert response.status_code == 200
    body = response.json()
    assert body["truncated"] is True
    assert len(body["text"]) == 40_000


@respx.mock
async def test_redirect_chain_is_followed_and_final_url_reported(client: AsyncClient) -> None:
    respx.get("http://example.com/start").mock(
        return_value=httpx.Response(302, headers={"location": "http://example.com/middle"})
    )
    respx.get("http://example.com/middle").mock(
        return_value=httpx.Response(302, headers={"location": "http://example.com/end"})
    )
    respx.get("http://example.com/end").mock(
        return_value=httpx.Response(200, text="landed", headers={"content-type": "text/plain"})
    )

    response = await client.get("/fetch", params={"url": "http://example.com/start"})

    assert response.status_code == 200
    body = response.json()
    assert body["final_url"] == "http://example.com/end"
    assert body["text"] == "landed"


@respx.mock
async def test_proxy_403_destination_denial_passed_through_as_502(client: AsyncClient) -> None:
    respx.get("http://192.168.1.1/").mock(
        return_value=httpx.Response(
            403,
            json={"error": "destination not allowed by egress policy"},
            headers={"content-type": "application/json"},
        )
    )

    response = await client.get("/fetch", params={"url": "http://192.168.1.1/"})

    assert response.status_code == 502
    body = response.json()
    assert body["upstream_status"] == 403
    assert "destination not allowed" in body["error"]


@respx.mock
async def test_upstream_4xx_passed_through_as_502(client: AsyncClient) -> None:
    respx.get("http://example.com/missing").mock(return_value=httpx.Response(404, text="not found"))

    response = await client.get("/fetch", params={"url": "http://example.com/missing"})

    assert response.status_code == 502
    body = response.json()
    assert body["upstream_status"] == 404
    assert "not found" in body["error"]


@respx.mock
async def test_upstream_5xx_passed_through_as_502(client: AsyncClient) -> None:
    respx.get("http://example.com/broken").mock(return_value=httpx.Response(500, text="boom"))

    response = await client.get("/fetch", params={"url": "http://example.com/broken"})

    assert response.status_code == 502
    assert response.json()["upstream_status"] == 500


@respx.mock
async def test_timeout_returns_504(client: AsyncClient) -> None:
    respx.get("http://example.com/slow").mock(side_effect=httpx.ConnectTimeout("timed out"))

    response = await client.get("/fetch", params={"url": "http://example.com/slow"})

    assert response.status_code == 504
    assert "error" in response.json()


async def test_non_http_scheme_rejected_with_400(client: AsyncClient) -> None:
    response = await client.get("/fetch", params={"url": "ftp://example.com/file"})
    assert response.status_code == 400
    assert "error" in response.json()


async def test_file_scheme_rejected_with_400(client: AsyncClient) -> None:
    response = await client.get("/fetch", params={"url": "file:///etc/passwd"})
    assert response.status_code == 400


async def test_missing_scheme_rejected_with_400(client: AsyncClient) -> None:
    response = await client.get("/fetch", params={"url": "example.com/no-scheme"})
    assert response.status_code == 400

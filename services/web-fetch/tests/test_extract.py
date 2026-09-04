"""Unit tests for `app/core/extract.py` — pure functions, no HTTP involved."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.core import extract as extract_module
from app.core.extract import base_content_type, extract

FIXTURES = Path(__file__).parent / "fixtures"


class TestBaseContentType:
    @pytest.mark.parametrize(
        ("header", "expected"),
        [
            ("text/html", "text/html"),
            ("text/html; charset=utf-8", "text/html"),
            ("APPLICATION/JSON", "application/json"),
            ("  text/plain ; charset=iso-8859-1", "text/plain"),
            (None, ""),
            ("", ""),
        ],
    )
    def test_strips_params_and_lowercases(self, header: str | None, expected: str) -> None:
        assert base_content_type(header) == expected


class TestExtractHtml:
    def test_trafilatura_happy_path(self) -> None:
        html = (FIXTURES / "sample.html").read_text()
        text, title = extract("text/html", html.encode())

        assert title == "Sample Article Title"
        assert "Sample Article Title" in text
        assert "[link](https://example.com/link)" in text  # include_links=True
        assert "Column A" in text  # include_tables=True

    def test_falls_back_to_readability_and_markdownify_when_trafilatura_empty(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(extract_module.trafilatura, "extract", lambda *a, **k: None)
        html = (FIXTURES / "sample.html").read_text()

        text, title = extract("text/html", html.encode())

        assert title == "Sample Article Title"
        assert "first paragraph of a small sample article" in text

    def test_missing_title_is_none(self) -> None:
        html = "<html><body><article><p>" + ("word " * 50) + "</p></article></body></html>"
        _, title = extract("text/html", html.encode())
        assert title is None


class TestExtractPlainTextLike:
    @pytest.mark.parametrize("content_type", ["text/plain", "text/markdown", "text/csv"])
    def test_passed_through_as_is(self, content_type: str) -> None:
        body = b"col_a,col_b\n1,2\n"
        text, title = extract(content_type, body)
        assert text == "col_a,col_b\n1,2\n"
        assert title is None

    def test_decodes_utf8_with_replacement_on_bad_bytes(self) -> None:
        text, _ = extract("text/plain", b"hello \xff world")
        assert "hello" in text and "world" in text


class TestExtractJson:
    def test_pretty_prints(self) -> None:
        body = json.dumps({"b": 2, "a": 1}).encode()
        text, title = extract("application/json", body)
        assert text == json.dumps({"b": 2, "a": 1}, indent=2, ensure_ascii=False)
        assert title is None

    def test_invalid_json_raises(self) -> None:
        with pytest.raises(json.JSONDecodeError):
            extract("application/json", b"{not json")


class TestExtractPdf:
    def test_extracts_text_from_fixture(self) -> None:
        body = (FIXTURES / "sample.pdf").read_bytes()
        text, title = extract("application/pdf", body)
        assert "Hello PDF fixture text" in text
        assert title is None

    def test_caps_at_50_pages(self, monkeypatch: pytest.MonkeyPatch) -> None:
        calls: list[int] = []

        class FakePage:
            def extract_text(self) -> str:
                calls.append(1)
                return "page text"

        class FakeReader:
            def __init__(self, _stream: object) -> None:
                self.pages = [FakePage() for _ in range(75)]

        monkeypatch.setattr(extract_module, "PdfReader", FakeReader)

        text, _ = extract("application/pdf", b"irrelevant")

        assert len(calls) == extract_module.PDF_MAX_PAGES
        assert text.count("page text") == extract_module.PDF_MAX_PAGES


def test_unsupported_content_type_raises_value_error() -> None:
    with pytest.raises(ValueError, match="unsupported content type"):
        extract("application/zip", b"whatever")

"""Scriptable state for the fake `web-fetch` service (`server.py`).

Records every `/search`/`/fetch` call a test drives through
`app.agent.web_tools`'s HTTP clients, and lets a test configure what each
responds with — mirrors `tests/fake_exec_manager/scripting.py`'s
`FakeExecManager` pattern (a plain, not-thread-safe, single-test-at-a-time
recorder driving an in-process ASGI app bound to a real ephemeral port, so
it works from `agent-server`'s own real httpx client the same way a real
`web-fetch` container would).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class SearchCall:
    q: str
    n: str | None


@dataclass
class FetchCall:
    url: str


class FakeWebFetch:
    def __init__(self) -> None:
        self.base_url: str = ""  # filled in by the `fake_web_fetch` fixture

        self.search_calls: list[SearchCall] = []
        self.search_response: dict = {"query": "", "results": []}
        self.search_status_code: int = 200

        self.fetch_calls: list[FetchCall] = []
        self.fetch_response: dict = {
            "url": "",
            "final_url": "",
            "title": None,
            "content_type": "text/plain",
            "text": "",
            "truncated": False,
            "fetched_at": "2026-09-04T00:00:00+00:00",
        }
        self.fetch_status_code: int = 200

    def record_search(self, q: str, n: str | None) -> None:
        self.search_calls.append(SearchCall(q, n))

    def record_fetch(self, url: str) -> None:
        self.fetch_calls.append(FetchCall(url))

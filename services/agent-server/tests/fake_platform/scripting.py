"""State for the fake platform (`server.py`): delegations and an in-memory files tree.

Just enough of the real one (services/platform) for agent-level tests:
`/personal/...` per user, `/spaces/<slug>/...` with a role per member, and
delegation tokens that stop working once their session is revoked. The
exact error wording is the real platform's job (tested there); this fake
only returns the same statuses and `detail` codes.

`client()` is an in-process `DelegationClient` over the same state, for
tests that don't need the HTTP exchange itself.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from app.core.delegation import DelegationDenied, DelegationUnavailable, Grant
from tests.fake_identity import TEST_USER_ID

AGENT_TOKEN = "fake-agent-service-token"
TEST_SESSION_ID = "11111111-1111-4111-8111-111111111111"


@dataclass
class Minted:
    user_id: str
    session_id: str
    thread_id: str
    expires_at: datetime


@dataclass
class FakePlatform:
    base_url: str = ""
    ttl: timedelta = timedelta(minutes=15)
    # identity token -> (user_id, session_id); unknown tokens are TEST_USER's.
    identities: dict[str, tuple[str, str]] = field(default_factory=dict)
    revoked_sessions: set[str] = field(default_factory=set)
    # Set to make the delegation endpoints answer 503 (platform trouble).
    unavailable: bool = False
    # space key ("personal:<user_id>" or a shared slug) -> {relative path: bytes}
    trees: dict[str, dict[str, bytes]] = field(default_factory=dict)
    # shared slug -> {user_id: role}
    members: dict[str, dict[str, str]] = field(default_factory=dict)
    grants: dict[str, Minted] = field(default_factory=dict)
    exchanges: list[tuple[str, str]] = field(default_factory=list)
    refreshes: list[str] = field(default_factory=list)
    # (method, path, bearer) for every files API request
    file_requests: list[tuple[str, str, str | None]] = field(default_factory=list)
    _counter: itertools.count = field(default_factory=itertools.count)

    # --- delegations ------------------------------------------------------------

    def _mint(self, user_id: str, session_id: str, thread_id: str) -> Grant:
        if session_id in self.revoked_sessions:
            raise DelegationDenied("unauthenticated")
        token = f"dlg-{next(self._counter)}-{user_id[:8]}"
        expires_at = datetime.now(UTC) + self.ttl
        self.grants[token] = Minted(user_id, session_id, thread_id, expires_at)
        return Grant(token, expires_at)

    def exchange(self, identity_token: str, thread_id: str) -> Grant:
        if self.unavailable:
            raise DelegationUnavailable("503")
        self.exchanges.append((identity_token, thread_id))
        user_id, session_id = self.identities.get(identity_token, (TEST_USER_ID, TEST_SESSION_ID))
        return self._mint(user_id, session_id, thread_id)

    def refresh(self, token: str) -> Grant:
        if self.unavailable:
            raise DelegationUnavailable("503")
        self.refreshes.append(token)
        minted = self.grants.get(token)
        if minted is None:
            raise DelegationDenied("unauthenticated")
        return self._mint(minted.user_id, minted.session_id, minted.thread_id)

    def principal(self, bearer: str | None) -> Minted | None:
        minted = self.grants.get(bearer or "")
        if minted is None or minted.session_id in self.revoked_sessions:
            return None
        if datetime.now(UTC) >= minted.expires_at:
            return None
        return minted

    def client(self) -> FakeDelegationClient:
        return FakeDelegationClient(self)

    # --- files ------------------------------------------------------------------

    def tree(self, key: str) -> dict[str, bytes]:
        return self.trees.setdefault(key, {})

    def personal(self, user_id: str = TEST_USER_ID) -> dict[str, bytes]:
        return self.tree(f"personal:{user_id}")

    def add_space(self, slug: str, roles: dict[str, str]) -> dict[str, bytes]:
        self.members[slug] = dict(roles)
        return self.tree(slug)


class FakeDelegationClient:
    def __init__(self, platform: FakePlatform) -> None:
        self.platform = platform

    async def exchange(self, identity_token: str, thread_id: str) -> Grant:
        return self.platform.exchange(identity_token, thread_id)

    async def refresh(self, token: str) -> Grant:
        return self.platform.refresh(token)

"""HITL approval markers: proof that a human approved an agent's destructive migration (§7).

An agent run (`act=agent`) may approve a pending destructive migration only
with a marker in `X-HomeAI-HITL-Approval`. agent-server mints one
(`POST /internal/hitl-approvals`, service token `PLATFORM_AGENT_TOKEN`)
from inside the `approve_migration` tool, after the run's LangGraph
interrupt came back with the user's approve decision from their chat
socket. The model never holds the service token, and the tool never puts
the marker in anything the model reads.

A marker is a random token, kept here only as its SHA-256 and bound to the
delegation's user, session and thread, one instance and one migration. It
lives `TTL` and is consumed by the first approve call that presents it,
whether that call then matches or not. The store is this process's memory
(the platform runs one worker): a restart drops every outstanding marker,
which fails closed.
"""

from __future__ import annotations

import hashlib
import secrets
import time
from collections.abc import Callable
from dataclasses import dataclass
from uuid import UUID

from app.core.errors import Forbidden
from app.core.principal import Principal

HEADER = "X-HomeAI-HITL-Approval"
PREFIX = "hitl_"
TTL_S = 60.0


@dataclass(frozen=True)
class _Grant:
    user_id: UUID
    session_id: UUID
    thread_id: str
    instance_id: UUID
    migration_id: UUID
    expires_at: float


def _digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class HitlApprovals:
    def __init__(self, clock: Callable[[], float] = time.monotonic, ttl_s: float = TTL_S) -> None:
        self._clock = clock
        self._ttl_s = ttl_s
        self._grants: dict[str, _Grant] = {}

    def _prune(self) -> None:
        now = self._clock()
        for key in [k for k, g in self._grants.items() if g.expires_at <= now]:
            del self._grants[key]

    def mint(
        self, principal: Principal, instance_id: UUID, migration_id: UUID
    ) -> tuple[str, float]:
        """A fresh marker for this delegation, instance and migration; returns (token, ttl_s)."""
        if not principal.is_agent or not principal.thread_id:
            raise Forbidden("hitl_approval_required")
        self._prune()
        token = PREFIX + secrets.token_urlsafe(32)
        self._grants[_digest(token)] = _Grant(
            principal.user_id,
            principal.session_id,
            principal.thread_id,
            instance_id,
            migration_id,
            self._clock() + self._ttl_s,
        )
        return token, self._ttl_s

    def consume(
        self, token: str | None, principal: Principal, instance_id: UUID, migration_id: UUID
    ) -> None:
        """Spend `token` on this approve call; `403 hitl_approval_required` unless it matches."""
        grant = self._grants.pop(_digest(token), None) if token else None
        if (
            grant is None
            or grant.expires_at <= self._clock()
            or (grant.user_id, grant.session_id, grant.thread_id)
            != (principal.user_id, principal.session_id, principal.thread_id)
            or (grant.instance_id, grant.migration_id) != (instance_id, migration_id)
        ):
            raise Forbidden("hitl_approval_required")

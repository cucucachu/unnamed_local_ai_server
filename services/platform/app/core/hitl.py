"""HITL approval markers: proof that a human approved one agent action (§7).

Two kinds of action take one: an agent approving a destructive migration,
and an integration tool call that asks first (M18-04,
docs/INTEGRATIONS.md §7). The migration kind is described below; an
integration call's marker is minted the same way
(`POST /internal/integration-approvals`, agent service token) and spent by
the integrations gateway (`.../consume`, gateway service token) on the
call it is bound to: one integration, one tool and the exact arguments
(compared as canonical JSON).

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
import json
import secrets
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
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
    # What the marker approves: ("migration", instance, migration) or
    # ("integration_call", integration, tool, arguments digest).
    subject: tuple
    expires_at: float


def _digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def arguments_digest(arguments: dict[str, Any]) -> str:
    """The same arguments give the same digest, whatever their key order."""
    canonical = json.dumps(arguments, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode()).hexdigest()


def _call(integration_id: UUID, tool: str, arguments: dict[str, Any]) -> tuple:
    return ("integration_call", integration_id, tool, arguments_digest(arguments))


class HitlApprovals:
    def __init__(self, clock: Callable[[], float] = time.monotonic, ttl_s: float = TTL_S) -> None:
        self._clock = clock
        self._ttl_s = ttl_s
        self._grants: dict[str, _Grant] = {}

    def _prune(self) -> None:
        now = self._clock()
        for key in [k for k, g in self._grants.items() if g.expires_at <= now]:
            del self._grants[key]

    def _mint(self, principal: Principal, subject: tuple) -> tuple[str, float]:
        if not principal.is_agent or not principal.thread_id:
            raise Forbidden("hitl_approval_required")
        self._prune()
        token = PREFIX + secrets.token_urlsafe(32)
        self._grants[_digest(token)] = _Grant(
            principal.user_id,
            principal.session_id,
            principal.thread_id,
            subject,
            self._clock() + self._ttl_s,
        )
        return token, self._ttl_s

    def _consume(self, token: str | None, principal: Principal, subject: tuple) -> None:
        grant = self._grants.pop(_digest(token), None) if token else None
        if (
            grant is None
            or grant.expires_at <= self._clock()
            or (grant.user_id, grant.session_id, grant.thread_id)
            != (principal.user_id, principal.session_id, principal.thread_id)
            or grant.subject != subject
        ):
            raise Forbidden("hitl_approval_required")

    def mint(
        self, principal: Principal, instance_id: UUID, migration_id: UUID
    ) -> tuple[str, float]:
        """A fresh marker for this delegation, instance and migration; returns (token, ttl_s)."""
        return self._mint(principal, ("migration", instance_id, migration_id))

    def consume(
        self, token: str | None, principal: Principal, instance_id: UUID, migration_id: UUID
    ) -> None:
        """Spend `token` on this approve call; `403 hitl_approval_required` unless it matches."""
        self._consume(token, principal, ("migration", instance_id, migration_id))

    def mint_call(
        self, principal: Principal, integration_id: UUID, tool: str, arguments: dict[str, Any]
    ) -> tuple[str, float]:
        """A fresh marker for one integration tool call with exactly these arguments."""
        return self._mint(principal, _call(integration_id, tool, arguments))

    def consume_call(
        self,
        token: str | None,
        principal: Principal,
        integration_id: UUID,
        tool: str,
        arguments: dict[str, Any],
    ) -> None:
        """Spend `token` on this call; `403 hitl_approval_required` unless it matches."""
        self._consume(token, principal, _call(integration_id, tool, arguments))

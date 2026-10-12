"""`needs_input` handles (docs/INTEGRATIONS.md §8, elicitation in chat).

When a server ends a call by asking the user for input, the gateway keeps
the server's request (and its opaque `requestState`) behind a short handle
and the agent asks the user in chat. The answer comes back with the handle
on a later call, and is accepted only if the thread has had a new user
message since: agent-server sends the id of the thread's latest user
message, so an answer in the same turn that received the request (which can
only be the model's own) is refused. Handles expire, and are this process's
memory: a restart drops them, and the agent starts the call again.
"""

from __future__ import annotations

import secrets
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from app.errors import GatewayError

PREFIX = "in_"


@dataclass
class Pending:
    user_id: UUID
    thread_id: str | None
    integration_id: UUID
    tool: str
    arguments: dict[str, Any]
    # Server-assigned keys to its input requests (elicitation form or URL).
    requests: dict[str, Any]
    request_state: str | None
    # The thread's latest user message when the server asked.
    asked_after: str | None
    # How the original call was let through ("allowed" or "approved").
    approval: str
    expires_at: float


class InputHandles:
    def __init__(self, ttl_s: float, clock: Callable[[], float] = time.monotonic) -> None:
        self._ttl_s = ttl_s
        self._clock = clock
        self._pending: dict[str, Pending] = {}

    def _prune(self) -> None:
        now = self._clock()
        for handle in [h for h, p in self._pending.items() if p.expires_at <= now]:
            del self._pending[handle]

    def create(self, **fields: Any) -> str:
        self._prune()
        handle = PREFIX + secrets.token_urlsafe(16)
        self._pending[handle] = Pending(**fields, expires_at=self._clock() + self._ttl_s)
        return handle

    def take(
        self,
        handle: str,
        user_id: UUID,
        thread_id: str | None,
        integration_id: UUID,
        tool: str,
        latest_user_message: str | None,
    ) -> Pending:
        """The pending call behind `handle`, if this caller may answer it now; it's spent."""
        self._prune()
        pending = self._pending.get(handle)
        if (
            pending is None
            or pending.user_id != user_id
            or pending.thread_id != thread_id
            or pending.integration_id != integration_id
            or pending.tool != tool
        ):
            raise GatewayError(
                "not_found", "This input handle is unknown or expired: start the call again."
            )
        if latest_user_message is None or latest_user_message == pending.asked_after:
            raise GatewayError(
                "refused_by_policy",
                "Only the user can answer: ask them and wait for their reply.",
            )
        del self._pending[handle]
        return pending

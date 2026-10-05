"""Delegation tokens for agent runs (docs/PLATFORM.md §4 "Delegation token", §6).

The chat socket exchanges the identity token it was opened with (5 min) for
a delegation (`POST /internal/delegations`, 15 min, `act=agent`,
`thr=<thread_id>`) and then refreshes it (`/internal/delegations/refresh`):
at the start of every turn and every approval resume, and in the background
whenever less than `REFRESH_MARGIN` is left, for as long as the socket is
open. The platform only mints either while the user's session is active, so
a revoked session stops the next turn and every file call in flight.

A scheduled routine run has no socket: it exchanges the routine's grant
(`/internal/routine-grants/exchange`, M17-03) instead of an identity token,
then refreshes the same way. That delegation reaches only the routine's
space.

`Delegation` is what a run's `config["configurable"]["delegation"]` holds.
It's an object, not the token string, on purpose: LangGraph copies every
`str`/`int`/`float`/`bool` in `configurable` into the checkpoint metadata it
writes to Postgres (and langchain into tracing metadata), and skips anything
else. The token itself is only read by `PlatformFilesBackend`, never put in
a prompt, a message, or tool arguments.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol

import httpx

logger = logging.getLogger(__name__)

REFRESH_MARGIN = timedelta(minutes=5)
RETRY_INTERVAL_S = 30.0


class DelegationDenied(Exception):
    """The platform refused: the session is revoked or expired, or the user is disabled."""


class DelegationUnavailable(Exception):
    """The platform couldn't be asked (unreachable, misconfigured, unexpected answer)."""


@dataclass(frozen=True)
class Grant:
    token: str
    expires_at: datetime


@dataclass(frozen=True)
class Caller:
    """Who asks the platform about spaces and routine grants: a person through the
    app (their identity token) or the agent in their chat (its delegation, M17-07)."""

    identity_token: str | None = None
    delegation_token: str | None = None

    def body(self) -> dict[str, str]:
        if self.delegation_token is not None:
            return {"delegation_token": self.delegation_token}
        return {"identity_token": self.identity_token or ""}

    def __repr__(self) -> str:
        return "Caller(delegation)" if self.delegation_token is not None else "Caller(identity)"


class DelegationClient(Protocol):
    async def exchange(self, identity_token: str, thread_id: str) -> Grant: ...

    async def refresh(self, token: str) -> Grant: ...

    async def space_role(self, caller: Caller, space: str) -> tuple[str, str] | None:
        """`(canonical space path, role)` of the caller's user, or None if
        `space` isn't one of theirs (M17-02)."""
        ...

    async def issue_routine_grant(
        self, caller: Caller, routine_id: str, space: str, label: str
    ) -> str | None:
        """A routine grant (M17-03) for a space the caller's user may edit, else None.

        A routine run's own delegation is refused (`DelegationDenied`).
        """
        ...

    async def exchange_routine_grant(self, grant: str, routine_id: str, thread_id: str) -> Grant:
        """A delegation for one run; `DelegationDenied` once the grant is dead."""
        ...

    async def revoke_routine_grant(self, grant: str) -> None: ...


class HttpDelegationClient:
    def __init__(self, platform_url: str, agent_token: str, timeout_s: float = 5.0) -> None:
        self._platform_url = platform_url
        self._agent_token = agent_token
        self._timeout_s = timeout_s

    async def _call(self, path: str, body: dict[str, str]) -> httpx.Response:
        if not self._agent_token:
            raise DelegationUnavailable("PLATFORM_AGENT_TOKEN is not set")
        try:
            async with httpx.AsyncClient(timeout=self._timeout_s) as client:
                response = await client.post(
                    f"{self._platform_url}{path}",
                    json=body,
                    headers={"Authorization": f"Bearer {self._agent_token}"},
                )
        except httpx.HTTPError as exc:
            raise DelegationUnavailable(repr(exc)) from exc
        if response.status_code == 401:
            raise DelegationDenied(response.text)
        return response

    async def _post(self, path: str, body: dict[str, str]) -> Grant:
        response = await self._call(path, body)
        if response.status_code != 200:
            raise DelegationUnavailable(f"{path}: HTTP {response.status_code}")
        payload = response.json()
        return Grant(payload["token"], datetime.fromisoformat(payload["expires_at"]))

    async def exchange(self, identity_token: str, thread_id: str) -> Grant:
        return await self._post(
            "/internal/delegations", {"identity_token": identity_token, "thread_id": thread_id}
        )

    async def refresh(self, token: str) -> Grant:
        return await self._post("/internal/delegations/refresh", {"token": token})

    async def space_role(self, caller: Caller, space: str) -> tuple[str, str] | None:
        path = "/internal/space-access"
        response = await self._call(path, caller.body() | {"space": space})
        if response.status_code == 403:
            raise DelegationDenied(response.text)
        if response.status_code in (404, 422):
            return None
        if response.status_code != 200:
            raise DelegationUnavailable(f"{path}: HTTP {response.status_code}")
        payload = response.json()
        return payload["space"], payload["role"]

    async def issue_routine_grant(
        self, caller: Caller, routine_id: str, space: str, label: str
    ) -> str | None:
        path = "/internal/routine-grants"
        body = caller.body() | {"routine_id": routine_id, "space": space, "label": label}
        response = await self._call(path, body)
        if response.status_code == 403 and "routine_run" in response.text:
            raise DelegationDenied(response.text)
        if response.status_code in (403, 404, 422):
            return None
        if response.status_code != 200:
            raise DelegationUnavailable(f"{path}: HTTP {response.status_code}")
        return response.json()["grant"]

    async def exchange_routine_grant(self, grant: str, routine_id: str, thread_id: str) -> Grant:
        return await self._post(
            "/internal/routine-grants/exchange",
            {"grant": grant, "routine_id": routine_id, "thread_id": thread_id},
        )

    async def revoke_routine_grant(self, grant: str) -> None:
        path = "/internal/routine-grants/revoke"
        response = await self._call(path, {"grant": grant})
        if response.status_code != 204:
            raise DelegationUnavailable(f"{path}: HTTP {response.status_code}")


class Delegation:
    """One chat connection's delegation, kept fresh while the connection lives."""

    def __init__(
        self,
        client: DelegationClient,
        grant: Grant,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._client = client
        self._grant = grant
        self._clock = clock
        self._sleep = sleep
        self._lock = asyncio.Lock()
        self.denied = False

    @classmethod
    async def obtain(
        cls, client: DelegationClient, identity_token: str, thread_id: str
    ) -> Delegation:
        return cls(client, await client.exchange(identity_token, thread_id))

    @property
    def token(self) -> str | None:
        """The current token, or None once refused or expired."""
        if self.denied or self._clock() >= self._grant.expires_at:
            return None
        return self._grant.token

    @property
    def expires_at(self) -> datetime:
        return self._grant.expires_at

    async def refresh(self) -> None:
        """Re-mint now; raises `DelegationDenied` (for good) or `DelegationUnavailable`."""
        async with self._lock:
            if self.denied:
                raise DelegationDenied("delegation was refused")
            try:
                self._grant = await self._client.refresh(self._grant.token)
            except DelegationDenied:
                self.denied = True
                raise

    async def keep_alive(
        self, margin: timedelta = REFRESH_MARGIN, retry_s: float = RETRY_INTERVAL_S
    ) -> None:
        """Refresh whenever less than `margin` is left, until refused; run as a task."""
        while not self.denied:
            wait = (self._grant.expires_at - margin - self._clock()).total_seconds()
            if wait > 0:
                await self._sleep(wait)
                continue
            try:
                await self.refresh()
            except DelegationDenied:
                logger.info("delegation: refresh refused; the session has ended")
                return
            except DelegationUnavailable as exc:
                logger.warning("delegation: refresh failed, retrying: %s", exc)
                await self._sleep(retry_s)

    def __copy__(self) -> Delegation:
        return self

    def __deepcopy__(self, memo: dict) -> Delegation:
        return self

    def __repr__(self) -> str:
        return f"Delegation(expires_at={self._grant.expires_at.isoformat()}, denied={self.denied})"

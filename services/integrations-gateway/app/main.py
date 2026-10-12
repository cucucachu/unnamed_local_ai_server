"""integrations-gateway: the only component that talks to MCP servers (docs/INTEGRATIONS.md §3).

Two callers, each with its own credential:

- the platform (`INTEGRATIONS_GATEWAY_TOKEN`): `POST /encrypt`, `POST /discover`;
- agent-server, with the user's delegation as the bearer: `POST /v1/open`,
  `/v1/call`, `/v1/read`, `/v1/prompt`. Results and standard errors are both
  `200` (`{"ok": true, ...}` / `{"ok": false, "error": {"code", "message"}}`);
  a bad delegation is `401` and an unreachable platform `503`.

A stopped turn closes its request; the call to the server is cancelled with it.
"""

from __future__ import annotations

import hmac
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Annotated, Any, Literal
from uuid import UUID

import anyio
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app.config import Settings
from app.connector import HttpConnector
from app.crypto import CredentialKey
from app.errors import GatewayError, PlatformUnavailable, Unauthorized
from app.handles import InputHandles
from app.platform import HttpPlatform
from app.service import Gateway

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
# httpx logs every request URL at INFO; keep URLs (which can carry a server's query secrets) out.
logging.getLogger("httpx2").setLevel(logging.WARNING)
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger("integrations-gateway")

Context = Literal["chat", "routine", "app"] | None
Slug = Annotated[str, Field(pattern=r"^[a-z][a-z0-9-]{0,39}$")]
Name = Annotated[str, Field(pattern=r"^[A-Za-z0-9_.\-]{1,128}$")]


class EncryptIn(BaseModel):
    integration_id: UUID
    user_id: UUID | None
    secret: Annotated[str, Field(min_length=1, max_length=8192)]


class DiscoverIn(BaseModel):
    integration: dict[str, Any]
    credential: dict[str, Any] | None = None


class OpenIn(BaseModel):
    integration: Slug
    context: Context = None


class InputIn(BaseModel):
    handle: Annotated[str, Field(max_length=100)]
    responses: dict[str, dict[str, Any]] = {}


class CallIn(BaseModel):
    integration: Slug
    tool: Name
    arguments: dict[str, Any] = {}
    context: Context = None
    approval: Annotated[str, Field(max_length=200)] | None = None
    input: InputIn | None = None
    latest_user_message: Annotated[str, Field(max_length=200)] | None = None


class ReadIn(BaseModel):
    integration: Slug
    uri: Annotated[str, Field(min_length=1, max_length=2000)]
    context: Context = None


class PromptIn(BaseModel):
    integration: Slug
    name: Name
    arguments: dict[str, Any] = {}
    context: Context = None


def _gateway(request: Request) -> Gateway:
    return request.app.state.gateway


GatewayDep = Annotated[Gateway, Depends(_gateway)]


def build(settings: Settings, gateway: Gateway | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        if app.state.gateway is None:
            app.state.gateway = Gateway(
                settings,
                CredentialKey.load_or_create(settings.key_file),
                HttpPlatform(settings.platform_url, settings.platform_gateway_token),
                HttpConnector(
                    settings.egress_proxy_url,
                    settings.ca_bundle,
                    settings.connect_timeout_s,
                    settings.call_timeout_s,
                ),
                InputHandles(settings.input_handle_ttl_s),
            )
        yield

    app = FastAPI(title="integrations-gateway", lifespan=lifespan)
    app.state.gateway = gateway

    def _bearer(request: Request) -> str:
        scheme, _, value = request.headers.get("authorization", "").partition(" ")
        if scheme.lower() != "bearer" or not value:
            raise HTTPException(401, "unauthorized")
        return value

    def platform_caller(request: Request) -> None:
        token = settings.integrations_gateway_token
        if not token or not hmac.compare_digest(_bearer(request).encode(), token.encode()):
            raise HTTPException(401, "unauthorized")

    async def until_disconnect(
        request: Request, run: Callable[[], Awaitable[dict[str, Any]]]
    ) -> dict[str, Any] | None:
        """`run`, cancelled if the caller goes away first."""
        out: dict[str, Any] | None = None

        async with anyio.create_task_group() as tg:

            async def watch() -> None:
                while not await request.is_disconnected():
                    await anyio.sleep(0.5)
                logger.info("caller disconnected; cancelling")
                tg.cancel_scope.cancel()

            async def work() -> None:
                nonlocal out
                out = await run()
                tg.cancel_scope.cancel()

            tg.start_soon(watch)
            tg.start_soon(work)
        return out

    async def agent_op(request: Request, run: Callable[[str], Awaitable[dict[str, Any]]]):
        delegation = _bearer(request)
        try:
            out = await until_disconnect(request, lambda: run(delegation))
        except BaseExceptionGroup as group:
            if group.subgroup(Unauthorized):
                raise HTTPException(401, "unauthorized") from None
            if group.subgroup(PlatformUnavailable):
                raise HTTPException(503, "platform_unavailable") from None
            raise
        if out is None:
            return JSONResponse({"detail": "cancelled"}, status_code=499)
        return out

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/encrypt", dependencies=[Depends(platform_caller)])
    async def encrypt(body: EncryptIn, g: GatewayDep) -> dict[str, str]:
        return {"ciphertext": g.encrypt(body.integration_id, body.user_id, body.secret)}

    @app.post("/discover", dependencies=[Depends(platform_caller)])
    async def discover(body: DiscoverIn, g: GatewayDep):
        try:
            return await g.discover(body.integration, body.credential)
        except GatewayError as exc:
            return JSONResponse({"detail": exc.code}, status_code=422)

    @app.post("/v1/open")
    async def open_(body: OpenIn, request: Request, g: GatewayDep):
        return await agent_op(request, lambda d: g.open(d, body.integration, body.context))

    @app.post("/v1/call")
    async def call(body: CallIn, request: Request, g: GatewayDep):
        return await agent_op(
            request,
            lambda d: g.call(
                d,
                body.integration,
                body.tool,
                body.arguments,
                body.context,
                approval=body.approval,
                input_handle=body.input.handle if body.input else None,
                input_responses=body.input.responses if body.input else None,
                latest_user_message=body.latest_user_message,
            ),
        )

    @app.post("/v1/read")
    async def read(body: ReadIn, request: Request, g: GatewayDep):
        return await agent_op(
            request, lambda d: g.read(d, body.integration, body.uri, body.context)
        )

    @app.post("/v1/prompt")
    async def prompt(body: PromptIn, request: Request, g: GatewayDep):
        return await agent_op(
            request,
            lambda d: g.prompt(d, body.integration, body.name, body.arguments, body.context),
        )

    return app


app = build(Settings())

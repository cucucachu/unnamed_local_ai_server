"""What the gateway does (docs/INTEGRATIONS.md §3, §6–§11).

Every agent request starts with a grant from the platform: who's asking, the
server, the credential to use and what each tool may do for this user. The
gateway then talks to the server, enforces the parts that must not depend on
agent-server (tool availability, approval markers, input from the user only)
and records the action in the audit log.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import logging
import re
from typing import Any
from uuid import UUID

import anyio
import mcp_types as types
from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError

from app import results
from app.config import Settings
from app.connector import Connector, Session, guarded
from app.crypto import CredentialError, CredentialKey, auth_headers
from app.errors import GatewayError
from app.handles import InputHandles
from app.oauth import OAuth, OAuthError, RefreshRefused
from app.platform import Platform

logger = logging.getLogger(__name__)

MAX_TOOLS = 500
MAX_RESOURCES = 1000
MAX_PROMPTS = 500
MAX_PAGES = 50
MAX_SCHEMA_BYTES = 64 * 1024
MAX_ARGUMENT_ERRORS = 10
MCP_NAME = re.compile(r"^[A-Za-z0-9_.\-]{1,128}$")
SECRET_KEY = re.compile(
    r"pass(word)?|secret|token|api[_-]?key|authorization|credential", re.IGNORECASE
)


def redact(value: Any) -> Any:
    """Arguments as the audit log keeps them: values under secret-looking keys hidden."""
    if isinstance(value, dict):
        return {k: "[hidden]" if SECRET_KEY.search(str(k)) else redact(v) for k, v in value.items()}
    if isinstance(value, list):
        return [redact(v) for v in value]
    return value


def _clip(value: str | None, limit: int) -> str | None:
    return None if value is None else value[:limit]


def _dump(model: Any) -> dict[str, Any]:
    return model.model_dump(mode="json", by_alias=True, exclude_none=True)


async def _paged(fetch: Any, attr: str, limit: int) -> list[Any]:
    items: list[Any] = []
    cursor = None
    for _ in range(MAX_PAGES):
        page = await fetch(cursor=cursor)
        items.extend(getattr(page, attr))
        cursor = page.next_cursor
        if not cursor or len(items) >= limit:
            break
    if len(items) > limit:
        logger.warning("server lists more than %d %s; keeping the first", limit, attr)
    return items[:limit]


def _validation_errors(schema: dict[str, Any], arguments: dict[str, Any]) -> list[dict[str, str]]:
    try:
        Draft202012Validator.check_schema(schema)
    except SchemaError:
        logger.warning("tool input schema is invalid; leaving validation to the server")
        return []
    errors = sorted(Draft202012Validator(schema).iter_errors(arguments), key=lambda e: e.path)
    return [
        {"path": "/" + "/".join(str(p) for p in e.absolute_path), "message": e.message[:300]}
        for e in errors[:MAX_ARGUMENT_ERRORS]
    ]


def _checked(
    *,
    auth: str | None = None,
    problem: str | None = None,
    detail: str | None = None,
    certificate: str | None = None,
    protocol_version: str | None = None,
    server: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "reachable": problem is None,
        "auth": auth,
        "problem": problem,
        "detail": detail,
        "certificate": certificate,
        "protocol_version": protocol_version,
        "server": server,
    }


def _needs_input_detail(request: Any) -> dict[str, Any]:
    if not isinstance(request, types.ElicitRequest):
        raise GatewayError(
            "protocol_error", "The server asked for something other than user input."
        )
    params = request.params
    if isinstance(params, types.ElicitRequestURLParams):
        host = re.sub(r"^[a-z]+://", "", params.url).split("/")[0]
        return {"mode": "url", "message": params.message, "url": params.url, "host": host}
    return {"mode": "form", "message": params.message, "fields": params.requested_schema}


class Gateway:
    def __init__(
        self,
        settings: Settings,
        key: CredentialKey,
        platform: Platform,
        connector: Connector,
        handles: InputHandles,
        oauth: OAuth | None = None,
    ) -> None:
        self.settings = settings
        self.key = key
        self.platform = platform
        self.connector = connector
        self.handles = handles
        self.oauth = oauth
        self._background: set[asyncio.Task[None]] = set()

    # --- for the platform -----------------------------------------------------------------

    def encrypt(self, integration_id: UUID, user_id: UUID | None, secret: str) -> str:
        sealed = self.key.seal(integration_id, user_id, {"kind": "token", "value": secret})
        return base64.b64encode(sealed).decode()

    def _open(
        self, integration_id: UUID, credential: dict[str, Any]
    ) -> tuple[UUID | None, dict[str, Any]]:
        try:
            raw = base64.b64decode(credential["ciphertext"], validate=True)
            owner = UUID(str(credential["user_id"])) if credential.get("user_id") else None
            return owner, self.key.open(raw, integration_id, owner)
        except (CredentialError, binascii.Error, ValueError, KeyError) as exc:
            logger.warning("stored credential for %s doesn't open", integration_id)
            raise GatewayError("auth_expired") from exc

    async def _headers(
        self,
        integration: dict[str, Any],
        credential: dict[str, Any] | None,
        stale: str | None = None,
    ) -> dict[str, str]:
        """The credential's headers; an OAuth one refreshed if expiring, or if `stale` was refused."""
        if credential is None:
            return {}
        integration_id = UUID(str(integration["id"]))
        owner, secret = self._open(integration_id, credential)
        if secret.get("kind") == "oauth":
            secret = await self._fresh_oauth(integration_id, owner, secret, stale)
            return {"Authorization": f"Bearer {secret['access_token']}"}
        try:
            return auth_headers(secret, integration.get("token_header"))
        except CredentialError as exc:
            raise GatewayError("auth_expired") from exc

    async def _fresh_oauth(
        self,
        integration_id: UUID,
        owner: UUID | None,
        secret: dict[str, Any],
        stale: str | None,
    ) -> dict[str, Any]:
        if self.oauth is None or not isinstance(secret.get("access_token"), str):
            raise GatewayError("auth_expired")
        try:
            fresh = await self.oauth.refreshed(integration_id, owner, secret, stale)
        except RefreshRefused as exc:
            raise GatewayError("auth_expired") from exc
        except OAuthError as exc:
            code = "timeout" if exc.code == "timeout" else "unreachable"
            raise GatewayError(code, "Couldn't renew the sign-in.") from exc
        if fresh.get("generation", 0) > secret.get("generation", 0):
            sealed = base64.b64encode(self.key.seal(integration_id, owner, fresh)).decode()
            await self.platform.store_credential(integration_id, owner, sealed)
        return fresh

    async def discover(
        self, integration: dict[str, Any], credential: dict[str, Any] | None
    ) -> dict[str, Any]:
        """What a server offers, in the platform's `DiscoveryIn` shape."""
        return await self._discover_with(integration, await self._headers(integration, credential))

    async def _discover_with(
        self, integration: dict[str, Any], headers: dict[str, str]
    ) -> dict[str, Any]:
        fresh = {**integration, "protocol_version": None}
        async with (
            self.connector.session(fresh, headers, elicitation=False) as session,
            guarded(session.statuses, session.denials),
        ):
            with anyio.fail_after(self.settings.call_timeout_s):
                return await self._discover(session)

    async def check(self, integration: dict[str, Any]) -> dict[str, Any]:
        """Before an admin sets up credentials: is the server there, and what sign-in does it want.

        Connects without a credential. A server that answers needs none; one
        that answers 401 wants a token, or OAuth if its challenge points to
        protected-resource metadata. A certificate the proxy refused comes
        back with its fingerprint, for the admin to confirm.
        """
        fresh = {**integration, "protocol_version": None}
        try:
            with anyio.fail_after(self.settings.call_timeout_s):
                checked = await self._check(fresh)
                if checked["auth"] == "oauth" and self.oauth is not None:
                    try:
                        checked["oauth"] = (await self.oauth.discover(fresh)).summary()
                    except OAuthError as exc:
                        checked["oauth"] = {"problem": exc.code, "detail": exc.detail}
                return checked
        except TimeoutError:
            return _checked(problem="timeout")

    def _oauth(self) -> OAuth:
        if self.oauth is None:
            raise OAuthError("oauth_unsupported")
        return self.oauth

    async def oauth_start(
        self,
        integration: dict[str, Any],
        owner: UUID | None,
        started_by: dict[str, Any],
        return_url: str,
        client: dict[str, Any] | None,
    ) -> dict[str, Any]:
        fresh = {**integration, "protocol_version": None}
        with anyio.fail_after(self.settings.call_timeout_s):
            return await self._oauth().start(fresh, owner, started_by, return_url, client)

    async def oauth_finish(
        self, state: str, code: str | None, iss: str | None, error: str | None
    ) -> dict[str, Any]:
        """Exchange the code; the sealed credential for the platform to store, tried first."""
        with anyio.fail_after(self.settings.call_timeout_s):
            pending, secret = await self._oauth().finish(state, code, iss, error)
        integration_id = UUID(str(pending.integration["id"]))
        owner = pending.owner
        self._oauth().forget(integration_id, owner)
        sealed = base64.b64encode(self.key.seal(integration_id, owner, secret)).decode()
        checked, problem = True, None
        try:
            headers = {"Authorization": f"Bearer {secret['access_token']}"}
            await self._discover_with(pending.integration, headers)
        except GatewayError as exc:
            if exc.code == "auth_expired":
                raise OAuthError("credential_rejected") from exc
            checked, problem = False, exc.code
        return {
            "integration_id": integration_id,
            "owner": owner,
            "started_by": pending.started_by,
            "ciphertext": sealed,
            "checked": checked,
            "problem": problem,
        }

    async def oauth_revoke(
        self, integration: dict[str, Any], credential: dict[str, Any]
    ) -> dict[str, bool]:
        """Best effort, before the platform deletes an OAuth credential."""
        integration_id = UUID(str(integration["id"]))
        try:
            owner, secret = self._open(integration_id, credential)
        except GatewayError:
            return {"revoked": False}
        if secret.get("kind") != "oauth" or self.oauth is None:
            return {"revoked": False}
        secret = self.oauth.freshest(integration_id, owner, secret)
        self.oauth.forget(integration_id, owner)
        with anyio.move_on_after(self.settings.call_timeout_s):
            return {"revoked": await self.oauth.revoke(secret)}
        return {"revoked": False}

    async def _check(self, integration: dict[str, Any]) -> dict[str, Any]:
        try:
            async with (
                self.connector.session(integration, {}, elicitation=False) as session,
                guarded(session.statuses, session.denials, session.seen),
            ):
                client = session.client
                server = client.server_info
                return _checked(
                    auth="none",
                    protocol_version=client.protocol_version,
                    server=_dump(server) if server else {},
                )
        except GatewayError as exc:
            if exc.code == "auth_expired":
                challenge = exc.seen.get("challenge", "").lower()
                return _checked(auth="oauth" if "resource_metadata" in challenge else "token")
            denial = exc.seen.get("denial")
            if denial in ("certificate_untrusted", "certificate_changed"):
                return _checked(problem=denial, certificate=exc.seen.get("certificate"))
            if denial == "destination":
                return _checked(problem="address_refused")
            return _checked(problem=exc.code, detail=exc.detail)

    async def _discover(self, session: Session) -> dict[str, Any]:
        client = session.client
        caps = client.server_capabilities
        tools = await _paged(client.list_tools, "tools", MAX_TOOLS) if caps.tools else []
        resources = (
            await _paged(client.list_resources, "resources", MAX_RESOURCES)
            if caps.resources
            else []
        )
        prompts = await _paged(client.list_prompts, "prompts", MAX_PROMPTS) if caps.prompts else []
        found_tools = []
        for t in tools:
            schema = t.input_schema
            if not MCP_NAME.match(t.name) or len(str(schema)) > MAX_SCHEMA_BYTES:
                logger.warning("skipping a tool with an unusable name or oversized schema")
                continue
            found_tools.append(
                {
                    "name": t.name,
                    "title": _clip(t.title, 200),
                    "description": _clip(t.description or "", 10_000),
                    "input_schema": schema,
                    "annotations": _dump(t.annotations) if t.annotations else {},
                }
            )
        server = client.server_info
        return {
            "protocol_version": client.protocol_version,
            "server": _dump(server) if server else {},
            "instructions": _clip(client.session.instructions or "", 20_000),
            "tools": found_tools,
            "resources": [
                {
                    "uri": str(r.uri),
                    "name": r.name[:200],
                    "title": _clip(r.title, 200),
                    "description": _clip(r.description or "", 10_000),
                    "mime_type": _clip(r.mime_type, 200),
                }
                for r in resources
                if len(str(r.uri)) <= 2000 and r.name
            ],
            "prompts": [
                {
                    "name": p.name,
                    "title": _clip(p.title, 200),
                    "description": _clip(p.description or "", 10_000),
                    "arguments": [_dump(a) for a in (p.arguments or [])][:100],
                }
                for p in prompts
                if MCP_NAME.match(p.name)
            ],
        }

    # --- for the agent --------------------------------------------------------------------

    async def _granted(self, delegation: str, slug: str) -> tuple[dict[str, Any], dict[str, str]]:
        grant = await self.platform.grant(delegation, slug)
        if grant["status"] == "not_connected":
            raise GatewayError("not_connected")
        if grant["status"] == "needs_reconnecting":
            raise GatewayError("auth_expired")
        try:
            headers = await self._headers(grant["integration"], grant["credential"])
        except GatewayError as exc:
            if exc.code == "auth_expired":
                await self._needs_reconnecting(grant)
            raise
        return grant, headers

    async def _needs_reconnecting(self, grant: dict[str, Any]) -> None:
        credential = grant.get("credential")
        if credential is None:
            return
        owner = UUID(str(credential["user_id"])) if credential.get("user_id") else None
        await self.platform.mark_needs_reconnecting(UUID(str(grant["integration"]["id"])), owner)

    async def _audit(
        self,
        delegation: str,
        slug: str,
        action: str,
        target: str,
        arguments: dict[str, Any],
        context: str | None,
        approval: str | None,
        error: GatewayError | None,
    ) -> None:
        with anyio.CancelScope(shield=True):
            await self.platform.audit(
                delegation,
                {
                    "integration": slug,
                    "action": action,
                    "target": target[:2000],
                    "arguments": redact(arguments),
                    "context": context,
                    "approval": approval,
                    "status": "error" if error else "ok",
                    "error_code": error.code if error else None,
                },
            )

    async def _run(
        self,
        delegation: str,
        slug: str,
        action: str,
        target: str,
        arguments: dict[str, Any],
        context: str | None,
        op: Any,
    ) -> dict[str, Any]:
        """Run one agent operation: grant, session, standard errors, audit."""
        audit: dict[str, Any] = {"approval": None, "arguments": arguments}
        grant: dict[str, Any] | None = None
        try:
            grant, headers = await self._granted(delegation, slug)
            elicitation = action == "call"
            try:
                body = await self._session_op(grant, headers, elicitation, op, audit)
            except GatewayError as exc:
                # A server refusing an OAuth access token ran nothing: renew it, try once more.
                if exc.code != "auth_expired" or grant["integration"]["auth_method"] != "oauth":
                    raise
                stale = headers.get("Authorization", "").removeprefix("Bearer ")
                headers = await self._headers(grant["integration"], grant["credential"], stale)
                body = await self._session_op(grant, headers, elicitation, op, audit)
        except GatewayError as exc:
            if exc.code == "auth_expired" and grant is not None:
                await self._needs_reconnecting(grant)
            await self._audit(
                delegation,
                slug,
                action,
                target,
                audit["arguments"],
                context,
                audit["approval"],
                exc,
            )
            return exc.body()
        except BaseException as exc:
            if isinstance(exc, (asyncio.CancelledError, anyio.get_cancelled_exc_class())):
                cancelled = GatewayError("cancelled")
                await self._audit(
                    delegation,
                    slug,
                    action,
                    target,
                    audit["arguments"],
                    context,
                    audit["approval"],
                    cancelled,
                )
            raise
        await self._audit(
            delegation, slug, action, target, audit["arguments"], context, audit["approval"], None
        )
        return {"ok": True, **body}

    async def _session_op(
        self,
        grant: dict[str, Any],
        headers: dict[str, str],
        elicitation: bool,
        op: Any,
        audit: dict[str, Any],
    ) -> dict[str, Any]:
        async with self.connector.session(
            grant["integration"], headers, elicitation=elicitation
        ) as session:
            async with guarded(session.statuses, session.denials):
                with anyio.fail_after(self.settings.call_timeout_s):
                    body = await op(grant, session, audit)
            self._after(grant, headers, session)
        return body

    def _after(self, grant: dict[str, Any], headers: dict[str, str], session: Session) -> None:
        """A server that said its lists changed gets rediscovered, if one credential sees it all."""
        if not session.list_changed:
            return
        integration = grant["integration"]
        if integration["credential_model"] == "per_user":
            return
        task = asyncio.create_task(self._rediscover(integration, grant["credential"]))
        self._background.add(task)
        task.add_done_callback(self._background.discard)

    async def _rediscover(self, integration: dict[str, Any], credential: Any) -> None:
        try:
            discovery = await self.discover(integration, credential)
            await self.platform.store_discovery(UUID(str(integration["id"])), discovery)
        except Exception:
            logger.warning("rediscovery after list_changed failed", exc_info=True)

    async def _live_tools(self, session: Session) -> dict[str, types.Tool]:
        if not session.client.server_capabilities.tools:
            return {}
        return {t.name: t for t in await _paged(session.client.list_tools, "tools", MAX_TOOLS)}

    async def open(self, delegation: str, slug: str, context: str | None) -> dict[str, Any]:
        async def op(grant: dict[str, Any], session: Session, audit: dict[str, Any]) -> dict:
            client = session.client
            caps = client.server_capabilities
            live = await self._live_tools(session)
            tools = []
            for name, policy in grant["tools"].items():
                tool = live.get(name)
                if tool is None or not policy["available"]:
                    continue
                tools.append(
                    {
                        "name": name,
                        "description": tool.description or tool.title or "",
                        "input_schema": tool.input_schema,
                        "permission": policy["permission"],
                    }
                )
            resources = (
                await _paged(client.list_resources, "resources", MAX_RESOURCES)
                if caps.resources
                else []
            )
            prompts = (
                await _paged(client.list_prompts, "prompts", MAX_PROMPTS) if caps.prompts else []
            )
            return {
                "integration": grant["integration"]["slug"],
                "instructions": client.session.instructions or "",
                "tools": tools,
                "resources": [
                    {"uri": str(r.uri), "name": r.name, "description": r.description or ""}
                    for r in resources
                ],
                "prompts": [
                    {
                        "name": p.name,
                        "description": p.description or "",
                        "arguments": [_dump(a) for a in (p.arguments or [])],
                    }
                    for p in prompts
                ],
            }

        return await self._run(delegation, slug, "open", "", {}, context, op)

    async def call(
        self,
        delegation: str,
        slug: str,
        tool: str,
        arguments: dict[str, Any],
        context: str | None,
        approval: str | None = None,
        input_handle: str | None = None,
        input_responses: dict[str, Any] | None = None,
        latest_user_message: str | None = None,
    ) -> dict[str, Any]:
        async def op(grant: dict[str, Any], session: Session, audit: dict[str, Any]) -> dict:
            policy = grant["tools"].get(tool)
            if policy is None:
                raise GatewayError("not_found", f"This integration has no tool {tool!r}.")
            if not policy["available"]:
                raise GatewayError("refused_by_policy", "This tool is turned off.")
            integration_id = UUID(str(grant["integration"]["id"]))
            user_id = UUID(str(grant["user_id"]))
            responses = None
            request_state = None
            args = arguments
            if input_handle is not None:
                pending = self.handles.answerable(
                    input_handle,
                    user_id,
                    grant["thread_id"],
                    integration_id,
                    tool,
                    latest_user_message,
                )
                args = pending.arguments
                audit["arguments"] = args
                audit["approval"] = pending.approval
                request_state = pending.request_state
                responses = self._responses(pending.requests, input_responses or {})
                self.handles.spend(input_handle)

            live = (await self._live_tools(session)).get(tool)
            if live is None:
                raise GatewayError("not_found", f"The server no longer offers {tool!r}.")
            if input_handle is None:
                errors = _validation_errors(live.input_schema, args)
                if errors:
                    raise GatewayError("invalid_arguments", errors=errors)
                if policy["permission"] == "allow":
                    audit["approval"] = "allowed"
                elif approval and await self.platform.consume_approval(
                    delegation, approval, slug, tool, args
                ):
                    audit["approval"] = "approved"
                else:
                    raise GatewayError("approval_required")

            async def progress(done: float, total: float | None, message: str | None) -> None:
                logger.info("%s/%s progress %s of %s", slug, tool, done, total or "?")

            result = await session.client.session.call_tool(
                tool,
                args,
                progress_callback=progress,
                input_responses=responses,
                request_state=request_state,
                allow_input_required=True,
            )
            if isinstance(result, types.InputRequiredResult):
                return self._needs_input(grant, tool, args, result, latest_user_message, audit)
            text, trimmed = results.trim(results.tool_text(result), self.settings.result_max_chars)
            return {"is_error": bool(result.is_error), "text": text, "trimmed": trimmed}

        return await self._run(delegation, slug, "call", tool, arguments, context, op)

    def _responses(
        self, requests: dict[str, Any], answers: dict[str, Any]
    ) -> dict[str, types.ElicitResult]:
        out = {}
        for key in requests:
            answer = answers.get(key)
            if not isinstance(answer, dict) or answer.get("action") not in (
                "accept",
                "decline",
                "cancel",
            ):
                raise GatewayError(
                    "invalid_arguments",
                    f"Answer every request ({', '.join(requests)}) as "
                    '{"action": "accept", "content": {...fields...}}, or {"action": "decline"} '
                    'or {"action": "cancel"}. The same handle still works.',
                )
            content = answer.get("content") if answer["action"] == "accept" else None
            if content is not None and not isinstance(content, dict):
                raise GatewayError("invalid_arguments", "An answer's content must be an object.")
            out[key] = types.ElicitResult(action=answer["action"], content=content)
        return out

    def _needs_input(
        self,
        grant: dict[str, Any],
        tool: str,
        args: dict[str, Any],
        result: types.InputRequiredResult,
        latest_user_message: str | None,
        audit: dict[str, Any],
    ) -> dict[str, Any]:
        requests = result.input_requests or {}
        if not requests:
            raise GatewayError("protocol_error", "The server asked for input without saying what.")
        details = {key: _needs_input_detail(r) for key, r in requests.items()}
        handle = self.handles.create(
            user_id=UUID(str(grant["user_id"])),
            thread_id=grant["thread_id"],
            integration_id=UUID(str(grant["integration"]["id"])),
            tool=tool,
            arguments=args,
            requests=dict(requests),
            request_state=result.request_state,
            asked_after=latest_user_message,
            approval=audit["approval"] or "allowed",
        )
        raise GatewayError("needs_input", handle=handle, requests=details)

    async def read(self, delegation: str, slug: str, uri: str, context: str | None) -> dict:
        async def op(grant: dict[str, Any], session: Session, audit: dict[str, Any]) -> dict:
            if not session.client.server_capabilities.resources:
                raise GatewayError("not_found", "This integration has no resources.")
            result = await session.client.read_resource(uri)
            text, trimmed = results.trim(
                results.resource_text(result), self.settings.result_max_chars
            )
            return {"text": text, "trimmed": trimmed}

        return await self._run(delegation, slug, "read", uri, {}, context, op)

    async def prompt(
        self, delegation: str, slug: str, name: str, arguments: dict[str, Any], context: str | None
    ) -> dict:
        async def op(grant: dict[str, Any], session: Session, audit: dict[str, Any]) -> dict:
            if not session.client.server_capabilities.prompts:
                raise GatewayError("not_found", "This integration has no prompts.")
            if not all(isinstance(v, str) for v in arguments.values()):
                raise GatewayError("invalid_arguments", "Prompt arguments are strings.")
            result = await session.client.get_prompt(name, arguments)
            text, trimmed = results.trim(
                results.prompt_text(result), self.settings.result_max_chars
            )
            return {"text": text, "trimmed": trimmed}

        return await self._run(delegation, slug, "prompt", name, arguments, context, op)

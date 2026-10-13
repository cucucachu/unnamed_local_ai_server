"""The standard errors (docs/INTEGRATIONS.md §9): a short code and a sentence telling the model what to do."""

from __future__ import annotations

from typing import Any

MESSAGES = {
    "not_connected": (
        "The user hasn't connected this integration. Tell them to connect it in "
        "Settings → Integrations."
    ),
    "auth_expired": (
        "The integration rejected the stored credential. The user (or an admin, for a shared "
        "one) needs to reconnect it in Settings → Integrations."
    ),
    "not_found": "No such integration, tool, resource or prompt, or the tool is turned off.",
    "invalid_arguments": "The arguments don't match the tool's input schema. Fix them and retry.",
    "refused_by_policy": "This isn't allowed by the user's settings or was rejected. Don't retry.",
    "approval_required": (
        "This tool asks first: the call needs the user's approval on its card. Don't retry "
        "without it."
    ),
    "needs_input": "The server needs input from the user. Ask them, then call again with the handle.",
    "unreachable": "The server couldn't be reached. Tell the user; retrying right away won't help.",
    "timeout": "The server didn't answer in time. You may retry once.",
    "too_large": "The result was too large.",
    "server_error": "The server returned an error.",
    "protocol_error": "The server doesn't speak a supported MCP version, or broke the protocol.",
    "certificate_changed": (
        "The server's certificate changed since an admin confirmed it. An admin must re-confirm "
        "it in Settings → Integrations."
    ),
}


class GatewayError(Exception):
    """A standard error, returned to the agent as a tool result, never a failed request."""

    def __init__(self, code: str, detail: str | None = None, **extra: Any) -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail
        self.extra = extra
        # What the HTTP layer saw (proxy denial, presented certificate, auth
        # challenge), for the admin's server check; never part of `body()`.
        self.seen: dict[str, str] = {}

    def body(self) -> dict[str, Any]:
        message = MESSAGES[self.code]
        if self.detail:
            message = f"{message} {self.detail}"
        return {"ok": False, "error": {"code": self.code, "message": message, **self.extra}}


class Unauthorized(Exception):
    """The caller's delegation or service token isn't valid (HTTP 401, not a tool result)."""


class PlatformUnavailable(Exception):
    """The platform can't be asked (HTTP 503)."""

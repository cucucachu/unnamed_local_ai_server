"""Domain errors raised by `app/core/*` and shared by the API and the CLI.

Each carries a stable machine-readable `code`; the API renders it as
`{"detail": "<code>"}` with the status in `STATUS_BY_ERROR` (`app/main.py`),
the CLI prints it.
"""


class PlatformError(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class Unauthorized(PlatformError):
    """401: no valid credential (session, identity, password, setup code, invite, ...)."""


class Forbidden(PlatformError):
    """403: authenticated, but not allowed (step-up needed, agent act, wrong password, ...)."""


class InvalidInput(PlatformError):
    """422: a well-formed request whose values break a rule (username format, ...)."""


class InvalidApp(InvalidInput):
    """422 `invalid_app`: an app package that fails validation; the body adds `diagnostics`."""

    def __init__(self, diagnostics: list[dict[str, str]]) -> None:
        super().__init__("invalid_app")
        self.diagnostics = diagnostics


class Conflict(PlatformError):
    """409: the request conflicts with current state (username taken, last admin, ...)."""


class NotFound(PlatformError):
    """404: an id that doesn't exist (or that the caller may not see)."""


class UnsupportedMedia(PlatformError):
    """415: the file isn't the kind this route handles (a thumbnail for a non-video, ...)."""


class Unavailable(PlatformError):
    """503: a service this needs can't be reached right now (the app builder, ...)."""


class ServerError(PlatformError):
    """500: a failure the caller can't fix (ffmpeg couldn't decode a video, ...)."""

"""`PlatformFilesBackend`: the agent's file tools over the platform files API (docs/PLATFORM.md §6).

A `deepagents.backends.protocol.BackendProtocol` that holds no files and no
credentials of its own. Every call reads the in-flight run's delegation from
`langgraph.config.get_config()["configurable"]["delegation"]` (set per turn
by `app/api/chat_ws.py`) and presents it as `Authorization: Bearer` to
`/api/platform/files*`, so the platform decides what this user may see and
change. No delegation, no request: the call fails closed.

Split of work with the platform (docs/ARCHITECTURE.md §3 "Which side formats
what"): the platform slices lines, base64-encodes binaries, checks edits and
words those errors like deepagents; this side maps HTTP failures onto the
strings `FilesystemBackend` (deepagents 0.7.11) returns - `File '<path>' not
found` and so on - adds the trailing `/` on directories in `ls`, and leaves
the line-number gutter to the filesystem middleware. Nothing here raises:
like `FilesystemBackend`, a failure is a result the model reads.

Each operation is written once as a generator that yields `_Call`s and
receives `_Reply`s, then run by a sync (`httpx.Client`) or async
(`httpx.AsyncClient`) driver.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Generator
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Any, TypeVar

import httpx
from deepagents.backends.protocol import (
    FILE_NOT_FOUND,
    INVALID_PATH,
    IS_DIRECTORY,
    PERMISSION_DENIED,
    BackendProtocol,
    DeleteResult,
    EditResult,
    FileData,
    FileDownloadResponse,
    FileInfo,
    FileUploadResponse,
    GlobResult,
    GrepMatch,
    GrepResult,
    LsResult,
    ReadResult,
    WriteResult,
)
from langgraph.config import get_config

logger = logging.getLogger(__name__)

# Above the platform's own grep (15 s) and glob (5 s) budgets.
_HTTP_TIMEOUT_S = 60.0
_FILES = "/api/platform/files"
_TOP_LEVELS = ("personal", "spaces")
_WHERE = "paths start with /personal/ or /spaces/<slug>/"

T = TypeVar("T")


@dataclass(frozen=True)
class _Call:
    method: str
    path: str
    params: dict[str, str] | None = None
    json: Any = None
    content: bytes | None = None


@dataclass(frozen=True)
class _Reply:
    """A platform response; `status` 0 means no response (no delegation, or unreachable)."""

    status: int
    content: bytes = b""
    body: Any = field(default=None)
    reason: str | None = None

    @property
    def ok(self) -> bool:
        return 200 <= self.status < 300

    @property
    def detail(self) -> str | None:
        value = self.body.get("detail") if isinstance(self.body, dict) else None
        return value if isinstance(value, str) else None

    @property
    def message(self) -> str | None:
        value = self.body.get("message") if isinstance(self.body, dict) else None
        return value if isinstance(value, str) else None


_NO_DELEGATION = _Reply(0, reason="file access is unavailable for this run (no delegation)")

Op = Generator[_Call, _Reply, T]


def _reply(response: httpx.Response) -> _Reply:
    body = None
    if response.headers.get("content-type", "").startswith("application/json"):
        try:
            body = response.json()
        except ValueError:
            body = None
    return _Reply(response.status_code, response.content, body)


def _unreachable(exc: Exception) -> _Reply:
    logger.warning("platform files: request failed: %r", exc)
    return _Reply(0, reason=f"the platform is unreachable ({type(exc).__name__})")


def _delegation_token() -> str | None:
    try:
        configurable = get_config().get("configurable") or {}
    except RuntimeError:
        return None
    token = getattr(configurable.get("delegation"), "token", None)
    return token if isinstance(token, str) and token else None


def _outside_spaces(path: str | None) -> bool:
    parts = PurePosixPath(path or "/").parts[1:]
    return bool(parts) and parts[0] not in _TOP_LEVELS


def _reason(reply: _Reply, path: str | None = None) -> str:
    """Why a call failed, in words that fit after `Error ...ing '<path>': `."""
    if reply.status == 0:
        return reply.reason or "the platform is unreachable"
    if reply.status == 401:
        return "not authorized (the user's session has ended)"
    detail = reply.detail
    if reply.status == 403:
        if detail == "insufficient_role":
            return "Permission denied (read-only access to this space)"
        if detail == "read_only":
            return f"Permission denied (not writable; {_WHERE})"
        return f"Permission denied ({detail or 'forbidden'})"
    if reply.status == 404:
        if _outside_spaces(path):
            return f"No such file or directory ({_WHERE})"
        return "No such file or directory"
    if reply.status == 409:
        return {
            "is_a_directory": "Is a directory",
            "not_a_directory": "Not a directory",
            "already_exists": "File exists",
        }.get(detail or "", detail or "conflict")
    if reply.status == 422:
        return reply.message or (
            "invalid path" if detail == "invalid_path" else detail or "invalid"
        )
    return f"platform error {reply.status}"


def _not_found(path: str, text: str) -> str:
    return f"{text} ({_WHERE})" if _outside_spaces(path) else text


def _operation_error(reply: _Reply) -> str | None:
    """The FileOperationError code for a failed upload/download."""
    return {
        403: PERMISSION_DENIED,
        404: FILE_NOT_FOUND,
        409: IS_DIRECTORY,
        422: INVALID_PATH,
    }.get(reply.status) or _reason(reply)


def _file_info(item: dict) -> FileInfo:
    info: FileInfo = {"path": item["path"], "is_dir": bool(item.get("is_dir", False))}
    if item.get("size") is not None:
        info["size"] = int(item["size"])
    if item.get("modified_at") is not None:
        info["modified_at"] = str(item["modified_at"])
    return info


# --- operations (sans I/O) ---------------------------------------------------------


def _ls(path: str) -> Op[LsResult]:
    reply = yield _Call("GET", _FILES, params={"path": path})
    if reply.status == 404:
        stat = yield _Call("GET", f"{_FILES}/stat", params={"path": path})
        if stat.ok and (stat.body or {}).get("entry", {}).get("type") == "file":
            return LsResult(error=f"Path '{path}': not_a_directory")
        return LsResult(error=_not_found(path, f"Path '{path}': path_not_found"))
    if not reply.ok:
        return LsResult(error=f"Cannot list '{path}': {_reason(reply, path)}")
    entries: list[FileInfo] = []
    for e in reply.body["entries"]:
        is_dir = e["type"] == "dir"
        entries.append(
            {
                "path": e["path"] + ("/" if is_dir else ""),
                "is_dir": is_dir,
                "size": int(e.get("size") or 0),
                "modified_at": str(e["mtime"]),
            }
        )
    entries.sort(key=lambda info: info["path"])
    return LsResult(entries=entries)


def _read(file_path: str, offset: int, limit: int) -> Op[ReadResult]:
    reply = yield _Call(
        "POST", f"{_FILES}/read", json={"path": file_path, "offset": offset, "limit": limit}
    )
    if reply.status == 404:
        return ReadResult(error=_not_found(file_path, f"File '{file_path}' not found"))
    if reply.status == 422 and reply.message:
        return ReadResult(error=reply.message)
    if not reply.ok:
        return ReadResult(error=f"Error reading file '{file_path}': {_reason(reply, file_path)}")
    body = reply.body
    return ReadResult(
        file_data=FileData(content=body["content"], encoding=body["encoding"]),
        total_lines=body.get("total_lines"),
        start_line=body.get("start_line"),
        end_line=body.get("end_line"),
        next_offset=body.get("next_offset"),
        no_lines_requested=bool(body.get("no_lines_requested", False)),
    )


def _write(file_path: str, content: str) -> Op[WriteResult]:
    reply = yield _Call("POST", f"{_FILES}/write", json={"path": file_path, "content": content})
    if not reply.ok:
        return WriteResult(error=f"Error writing file '{file_path}': {_reason(reply, file_path)}")
    return WriteResult(path=file_path)


def _edit(file_path: str, old: str, new: str, replace_all: bool) -> Op[EditResult]:
    reply = yield _Call(
        "POST",
        f"{_FILES}/edit",
        json={"path": file_path, "old_string": old, "new_string": new, "replace_all": replace_all},
    )
    if reply.status == 404:
        return EditResult(error=_not_found(file_path, f"Error: File '{file_path}' not found"))
    if reply.status == 422 and reply.message:
        return EditResult(error=reply.message)
    if not reply.ok:
        return EditResult(error=f"Error editing file '{file_path}': {_reason(reply, file_path)}")
    return EditResult(path=file_path, occurrences=int(reply.body["occurrences"]))


def _delete(file_path: str) -> Op[DeleteResult]:
    reply = yield _Call("DELETE", _FILES, params={"path": file_path})
    if reply.status == 404:
        return DeleteResult(error=_not_found(file_path, f"Error: '{file_path}' not found"))
    if not reply.ok:
        return DeleteResult(error=f"Error deleting '{file_path}': {_reason(reply, file_path)}")
    return DeleteResult(path=file_path)


def _grep(
    pattern: str, path: str | None, glob: str | None, max_count: int | None
) -> Op[GrepResult]:
    body: dict[str, Any] = {"pattern": pattern, "path": path, "glob": glob, "max_count": max_count}
    reply = yield _Call(
        "POST", f"{_FILES}/grep", json={k: v for k, v in body.items() if v is not None}
    )
    if reply.status == 404 or (reply.status == 422 and reply.detail == "invalid_path"):
        return GrepResult(matches=[])
    if reply.status == 422 and reply.message:
        return GrepResult(error=reply.message, matches=[])
    if not reply.ok:
        where = path or "/"
        return GrepResult(
            error=f"Error searching path '{where}': {_reason(reply, path)}", matches=[]
        )
    matches: list[GrepMatch] = [
        {"path": m["path"], "line": int(m["line"]), "text": m["text"]}
        for m in reply.body["matches"]
    ]
    return GrepResult(
        error=reply.body.get("error"), matches=matches, truncated=bool(reply.body.get("truncated"))
    )


def _glob(pattern: str, path: str | None) -> Op[GlobResult]:
    body: dict[str, Any] = {"pattern": pattern}
    if path is not None:
        body["path"] = path
    reply = yield _Call("POST", f"{_FILES}/glob", json=body)
    if reply.status == 404 or (reply.status == 422 and reply.detail == "invalid_path"):
        return GlobResult(matches=[])
    if reply.status == 422 and reply.message:
        return GlobResult(error=reply.message, matches=None)
    if not reply.ok:
        where = path if path is not None else "<default>"
        return GlobResult(
            error=f"Error globbing path '{where}': {_reason(reply, path)}", matches=[]
        )
    return GlobResult(
        matches=[_file_info(m) for m in reply.body["matches"]],
        truncated=bool(reply.body.get("truncated")),
        truncation_reason=reply.body.get("truncation_reason"),
    )


def _upload(files: list[tuple[str, bytes]]) -> Op[list[FileUploadResponse]]:
    responses = []
    for path, content in files:
        reply = yield _Call("PUT", f"{_FILES}/content", params={"path": path}, content=content)
        responses.append(
            FileUploadResponse(path=path, error=None if reply.ok else _operation_error(reply))
        )
    return responses


def _download(paths: list[str]) -> Op[list[FileDownloadResponse]]:
    responses = []
    for path in paths:
        reply = yield _Call("GET", f"{_FILES}/download", params={"path": path})
        if reply.ok:
            responses.append(FileDownloadResponse(path=path, content=reply.content))
            continue
        error = _operation_error(reply)
        if reply.status == 404:
            stat = yield _Call("GET", f"{_FILES}/stat", params={"path": path})
            if stat.ok and (stat.body or {}).get("entry", {}).get("type") == "dir":
                error = IS_DIRECTORY
        responses.append(FileDownloadResponse(path=path, error=error))
    return responses


# --- the backend ---------------------------------------------------------------------


class PlatformFilesBackend(BackendProtocol):
    def __init__(self, platform_url: str, *, timeout_s: float = _HTTP_TIMEOUT_S) -> None:
        self._platform_url = platform_url
        self._timeout_s = timeout_s

    def _request_kwargs(self, call: _Call, token: str) -> dict[str, Any]:
        kwargs: dict[str, Any] = {
            "params": call.params,
            "headers": {"Authorization": f"Bearer {token}"},
        }
        if call.json is not None:
            kwargs["content"] = json.dumps(call.json).encode()
            kwargs["headers"]["Content-Type"] = "application/json"
        elif call.content is not None:
            kwargs["content"] = call.content
            kwargs["headers"]["Content-Type"] = "application/octet-stream"
        return kwargs

    def _run(self, op: Op[T]) -> T:
        token = _delegation_token()
        client = None
        try:
            call = next(op)
            while True:
                if token is None:
                    reply = _NO_DELEGATION
                else:
                    if client is None:
                        client = httpx.Client(base_url=self._platform_url, timeout=self._timeout_s)
                    try:
                        response = client.request(
                            call.method, call.path, **self._request_kwargs(call, token)
                        )
                        reply = _reply(response)
                    except httpx.HTTPError as exc:
                        reply = _unreachable(exc)
                call = op.send(reply)
        except StopIteration as done:
            return done.value
        finally:
            if client is not None:
                client.close()

    async def _arun(self, op: Op[T]) -> T:
        token = _delegation_token()
        client = None
        try:
            call = next(op)
            while True:
                if token is None:
                    reply = _NO_DELEGATION
                else:
                    if client is None:
                        client = httpx.AsyncClient(
                            base_url=self._platform_url, timeout=self._timeout_s
                        )
                    try:
                        response = await client.request(
                            call.method, call.path, **self._request_kwargs(call, token)
                        )
                        reply = _reply(response)
                    except httpx.HTTPError as exc:
                        reply = _unreachable(exc)
                call = op.send(reply)
        except StopIteration as done:
            return done.value
        finally:
            if client is not None:
                await client.aclose()

    def ls(self, path: str) -> LsResult:
        return self._run(_ls(path))

    async def als(self, path: str) -> LsResult:
        return await self._arun(_ls(path))

    def read(self, file_path: str, offset: int = 0, limit: int = 2000) -> ReadResult:
        return self._run(_read(file_path, offset, limit))

    async def aread(self, file_path: str, offset: int = 0, limit: int = 2000) -> ReadResult:
        return await self._arun(_read(file_path, offset, limit))

    def write(self, file_path: str, content: str) -> WriteResult:
        return self._run(_write(file_path, content))

    async def awrite(self, file_path: str, content: str) -> WriteResult:
        return await self._arun(_write(file_path, content))

    def edit(
        self, file_path: str, old_string: str, new_string: str, replace_all: bool = False
    ) -> EditResult:
        return self._run(_edit(file_path, old_string, new_string, replace_all))

    async def aedit(
        self, file_path: str, old_string: str, new_string: str, replace_all: bool = False
    ) -> EditResult:
        return await self._arun(_edit(file_path, old_string, new_string, replace_all))

    def delete(self, file_path: str) -> DeleteResult:
        return self._run(_delete(file_path))

    async def adelete(self, file_path: str) -> DeleteResult:
        return await self._arun(_delete(file_path))

    def grep(
        self,
        pattern: str,
        path: str | None = None,
        glob: str | None = None,
        *,
        max_count: int | None = None,
    ) -> GrepResult:
        return self._run(_grep(pattern, path, glob, max_count))

    async def agrep(
        self,
        pattern: str,
        path: str | None = None,
        glob: str | None = None,
        *,
        max_count: int | None = None,
    ) -> GrepResult:
        return await self._arun(_grep(pattern, path, glob, max_count))

    def glob(self, pattern: str, path: str | None = None) -> GlobResult:
        return self._run(_glob(pattern, path))

    async def aglob(self, pattern: str, path: str | None = None) -> GlobResult:
        return await self._arun(_glob(pattern, path))

    def upload_files(self, files: list[tuple[str, bytes]]) -> list[FileUploadResponse]:
        return self._run(_upload(files))

    async def aupload_files(self, files: list[tuple[str, bytes]]) -> list[FileUploadResponse]:
        return await self._arun(_upload(files))

    def download_files(self, paths: list[str]) -> list[FileDownloadResponse]:
        return self._run(_download(paths))

    async def adownload_files(self, paths: list[str]) -> list[FileDownloadResponse]:
        return await self._arun(_download(paths))

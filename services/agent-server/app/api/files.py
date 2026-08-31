"""REST file management over the shared workspace directory — `/api/files*`.

Contract fixed by the "Reference: Shared Conventions & Contracts" issue
(#34), §5 "Files" — do not deviate from the shapes below. §8's traversal
guard (`app.core.paths.resolve_workspace_path`) is applied to EVERY `path`/
`src`/`dst` parameter before touching the filesystem; this is a SEPARATE,
human-facing API over the same real host directory the agent's own
`deepagents.backends.FilesystemBackend` (`app/agent/build.py`) already reads/
writes — no code is shared with `FilesystemBackend` itself, only the
directory on disk (the "three consumers, one directory" invariant, see
README.md and `docker-compose.yml`'s `agent-server.volumes` bind mount).

## Introspection notes (real, not guessed — installed versions: `fastapi==
0.141.1`, `starlette==1.6.0`, `anyio==4.14.2`, Python 3.12 stdlib; see this
ticket's final report for the full transcript):

- `python-multipart` was NOT installed prior to this ticket (`import
  multipart` raised `ModuleNotFoundError`, `importlib.metadata.version(
  "python-multipart")` raised `PackageNotFoundError`) — FastAPI needs it to
  parse `multipart/form-data` (`UploadFile`/`Form`); added via `uv add
  python-multipart` (now `python-multipart==0.0.32` in `pyproject.toml`/
  `uv.lock`). `anyio` WAS already present transitively (pulled in by
  `starlette`) and importable with no separate dependency needed.
- `anyio.to_thread.run_sync(func, *args)` (confirmed via
  `inspect.signature`) runs a plain sync callable in the worker thread pool
  and awaits its result — used below for `mkdir`/`move`/`copy`/`delete`
  (arbitrarily large/slow filesystem mutations) so a big `copytree` can't
  block the event loop for every other in-flight request/WS connection.
  Upload's own chunk-read-then-write loop is deliberately NOT wrapped in
  `anyio.to_thread.run_sync`: each iteration's `await file.read(1MiB)`
  already yields control back to the loop between chunks (unlike a single
  giant synchronous `copytree` call, which can't yield at all until it's
  done), and each chunk's synchronous `write()` is bounded to <=1MiB, so the
  worst-case per-iteration block is negligible — matching the ticket's own
  `await file.read(1024*1024)`-loop framing for upload specifically (a
  streaming concern) as distinct from the "run in a thread" framing used for
  the other four mutating endpoints (a big-single-syscall concern).
- `starlette.responses.FileResponse(path, filename=name)` (reading its
  `__init__` source directly) sets `Content-Disposition: attachment;
  filename="<name>"` via `self.headers.setdefault(...)` whenever `filename`
  is given and content_disposition_type defaults to `"attachment"` — no
  extra header wiring needed here, just passing `filename=`.
- `shutil.move(src, dst)`'s "moving a directory into itself" guard
  (`shutil.Error("Cannot move a directory '...' into itself '...'.")`) only
  fires from within its own `except OSError:` branch, which is only reached
  when the initial `os.rename(src, dst)` attempt fails — confirmed this
  really does happen for a same-filesystem self-nested move (e.g. moving
  `a` to `a/sub/moved`): POSIX `rename(2)` itself refuses (`EINVAL`, a
  directory can't become its own descendant), Python surfaces that as
  `OSError`, `shutil.move` catches it, detects `isdir(src)` +
  `_destinsrc(src, dst)`, and raises `shutil.Error` — verified by actually
  running this against a real tmp directory rather than assuming from
  `shutil`'s docstring. `shutil.Error` is a direct `OSError` subclass, so
  it's caught specifically (not via a broad `except OSError`) and mapped to
  `400` below; this route's own pre-checks (`dst` must not already exist)
  mean the only way `shutil.Error` reaches this handler in practice is this
  self-nesting case.
- `path: str = Form(...)` (i.e. a REQUIRED form field) treats an explicitly
  sent, present-but-EMPTY-string value (`data={"path": ""}` — exactly what a
  client uploading to the workspace root must send, since `""` means root
  per §5/§8) as if the field were absent entirely: FastAPI 0.141.1 /
  pydantic 2.13.5 raise `422 {"detail": [{"type": "missing", "loc": ["body",
  "path"], ...}]}` — confirmed directly with a minimal scratch endpoint
  using ONLY `path: str = Form(...)` (no file upload involved at all), so
  this isn't a `File`-interaction quirk, it's `Form(...)`-with-empty-string
  specifically. Switching to `path: str = Form("")` (a default of `""`
  rather than the `...` "required" sentinel) fixes it — re-tested against
  the same scratch endpoint: an explicit `data={"path": ""}` now correctly
  round-trips to `path == ""`, an explicit non-empty value still works
  unchanged, and even a wholly-omitted field also defaults to `""` (also
  correct here, since an omitted upload target dir should mean "root" too).
- `Path.lstat()` never follows the final path component even when it's a
  symlink (unlike `Path.stat()`/`Path.is_dir()`) — used for EVERY listed
  entry's type/size/mtime per the ticket's explicit instruction ("do NOT
  follow directory symlinks when sizing"): a symlink entry (to a file OR a
  directory) is therefore always typed `"file"` here with `size` = the
  symlink's own inode size (the length of its target string), never the
  size of whatever it points to, and listing a directory never stats
  through any symlink it contains. This is deliberately conservative:
  reading a directory listing can never be tricked into following a
  symlink to something outside (or expensive/circular within) the
  workspace.
"""

from __future__ import annotations

import mimetypes
import os
import shutil
import stat
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

import anyio.to_thread
from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel

from app.core.paths import resolve_workspace_path

router = APIRouter()

_UPLOAD_CHUNK_SIZE = 1024 * 1024  # 1 MiB, per the ticket's exact spec.


class FileEntryOut(BaseModel):
    name: str
    path: str
    type: Literal["file", "dir"]
    size: int
    mtime: str
    mime: str | None = None


class FileListOut(BaseModel):
    path: str
    entries: list[FileEntryOut]


class UploadOut(BaseModel):
    uploaded: list[str]


class MkdirBody(BaseModel):
    path: str


class MoveCopyBody(BaseModel):
    src: str
    dst: str


def _workspace_root(request: Request) -> Path:
    return Path(request.app.state.settings.workspace_root)


def _rel_posix(root: Path, p: Path) -> str:
    """`p`'s workspace-relative POSIX path — `""` when `p` is `root` itself."""
    if p == root:
        return ""
    return p.relative_to(root).as_posix()


def _entry_out(root: Path, parent: Path, entry: Path) -> FileEntryOut:
    # `lstat`, not `stat`/`is_dir` — see module docstring's introspection
    # note on why symlinks are never followed here.
    st = entry.lstat()
    is_dir = stat.S_ISDIR(st.st_mode)
    rel_dir = _rel_posix(root, parent)
    rel_path = f"{rel_dir}/{entry.name}" if rel_dir else entry.name
    return FileEntryOut(
        name=entry.name,
        path=rel_path,
        type="dir" if is_dir else "file",
        size=0 if is_dir else st.st_size,
        mtime=datetime.fromtimestamp(st.st_mtime, tz=UTC).isoformat(),
        mime=mimetypes.guess_type(entry.name)[0],
    )


@router.get("/files", response_model=FileListOut)
async def list_files(request: Request, path: str = "") -> FileListOut:
    root = _workspace_root(request)
    target = resolve_workspace_path(root, path)
    if not target.is_dir():
        raise HTTPException(status_code=404, detail=f"directory '{path}' not found")

    entries = [_entry_out(root, target, entry) for entry in target.iterdir()]
    entries.sort(key=lambda e: (e.type != "dir", e.name.lower()))
    return FileListOut(path=_rel_posix(root, target), entries=entries)


@router.post("/files/upload", status_code=201, response_model=UploadOut)
async def upload_files(
    request: Request,
    path: str = Form(""),
    file: list[UploadFile] = File(...),  # noqa: B008 - required FastAPI dependency-injection idiom
) -> UploadOut:
    root = _workspace_root(request)
    target_dir = resolve_workspace_path(root, path)
    if not target_dir.is_dir():
        raise HTTPException(status_code=404, detail=f"directory '{path}' not found")

    rel_dir = _rel_posix(root, target_dir)
    uploaded: list[str] = []
    for upload in file:
        # Sanitize with `os.path.basename` first (strips any client-supplied
        # directory components from the multipart filename), THEN re-run it
        # through the same traversal guard as every other endpoint (belt and
        # suspenders against a bare "." / ".." filename, which `basename`
        # alone does not neutralize: `Path(target_dir) / ".."` would
        # otherwise silently escape `target_dir` to its parent).
        filename = os.path.basename(upload.filename or "")
        if not filename or filename in (".", ".."):
            raise HTTPException(status_code=400, detail="invalid upload filename")
        dest_rel = f"{rel_dir}/{filename}" if rel_dir else filename
        dest = resolve_workspace_path(root, dest_rel)

        # Streamed in fixed 1 MiB chunks, no full-file buffering — see
        # module docstring for why this loop (unlike mkdir/move/copy/delete)
        # is not additionally wrapped in `anyio.to_thread.run_sync`.
        with dest.open("wb") as f:
            while chunk := await upload.read(_UPLOAD_CHUNK_SIZE):
                f.write(chunk)
        uploaded.append(dest_rel)

    return UploadOut(uploaded=uploaded)


@router.get("/files/download")
async def download_file(request: Request, path: str) -> FileResponse:
    root = _workspace_root(request)
    target = resolve_workspace_path(root, path)
    if not target.is_file():
        raise HTTPException(status_code=404, detail=f"file '{path}' not found")
    return FileResponse(target, filename=target.name)


@router.post("/files/mkdir", status_code=201)
async def mkdir(request: Request, body: MkdirBody) -> dict[str, str]:
    root = _workspace_root(request)
    target = resolve_workspace_path(root, body.path)

    def _mkdir() -> None:
        target.mkdir(parents=True, exist_ok=True)

    try:
        await anyio.to_thread.run_sync(_mkdir)
    except FileExistsError as exc:
        # `exist_ok=True` only suppresses the "already exists" error when
        # the existing entry is itself a directory — a pre-existing FILE at
        # `target` still raises, which we surface as a conflict.
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"path": _rel_posix(root, target)}


def _check_move_copy_preconditions(root: Path, src: Path, dst: Path, src_label: str, dst_label: str) -> None:
    if not src.exists():
        raise HTTPException(status_code=404, detail=f"source '{src_label}' not found")
    if dst.exists():
        raise HTTPException(status_code=409, detail=f"destination '{dst_label}' already exists")
    if not dst.parent.exists():
        raise HTTPException(
            status_code=400, detail=f"destination parent of '{dst_label}' does not exist"
        )


@router.post("/files/move")
async def move_path(request: Request, body: MoveCopyBody) -> dict[str, str]:
    root = _workspace_root(request)
    src = resolve_workspace_path(root, body.src)
    dst = resolve_workspace_path(root, body.dst)
    _check_move_copy_preconditions(root, src, dst, body.src, body.dst)

    def _move() -> None:
        shutil.move(str(src), str(dst))

    try:
        await anyio.to_thread.run_sync(_move)
    except shutil.Error as exc:
        # See module docstring: the only way `shutil.Error` reaches here
        # (given the preconditions above already ruled out "dst exists") is
        # the "moving a directory into itself" case.
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return {"src": _rel_posix(root, src), "dst": _rel_posix(root, dst)}


@router.post("/files/copy")
async def copy_path(request: Request, body: MoveCopyBody) -> dict[str, str]:
    root = _workspace_root(request)
    src = resolve_workspace_path(root, body.src)
    dst = resolve_workspace_path(root, body.dst)
    _check_move_copy_preconditions(root, src, dst, body.src, body.dst)

    def _copy() -> None:
        if src.is_dir():
            shutil.copytree(src, dst)
        else:
            shutil.copy2(src, dst)

    await anyio.to_thread.run_sync(_copy)
    return {"src": _rel_posix(root, src), "dst": _rel_posix(root, dst)}


@router.delete("/files", status_code=204)
async def delete_path(request: Request, path: str) -> None:
    root = _workspace_root(request)
    target = resolve_workspace_path(root, path)

    if target == root:
        raise HTTPException(status_code=400, detail="cannot delete the workspace root")
    if not target.exists():
        raise HTTPException(status_code=404, detail=f"path '{path}' not found")

    def _delete() -> None:
        if target.is_dir():
            shutil.rmtree(target)
        else:
            target.unlink()

    await anyio.to_thread.run_sync(_delete)

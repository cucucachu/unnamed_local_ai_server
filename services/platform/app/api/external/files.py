"""`/api/platform/files*`: the files API over virtual paths (docs/PLATFORM.md §5).

Every `path`/`src`/`dst` goes through `vfs.resolve_virtual_path` (membership,
role, traversal, symlink escape) before the filesystem is touched; reads
need `read` (viewer), anything that changes a space needs `write` (editor),
a cross-space move/copy needs `write` on both. Files and directories created
here belong to the caller's uid and the space's gid (`app.core.fsops`).
The filesystem itself is only ever reached below the space's open `files/`
fd (`Resolved.open_root`, `app.core.beneath`), never by host path.

Two groups of routes, one contract (docs/ARCHITECTURE.md §3 "Files"):
the file manager's (list, stat, upload, download, mkdir, move, copy,
rename, delete - the shapes of agent-server's old `/api/files*`), and the
agent file tools' (`read`, `write`, `edit`, `grep`, `glob`, `content`),
which mirror deepagents' `FilesystemBackend` via `app.core.agentfs`.
Blocking filesystem work runs in a worker thread.
"""

from __future__ import annotations

import errno
import mimetypes
import os
import stat
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import BinaryIO

import anyio.to_thread
from fastapi import APIRouter, File, Form, Request, UploadFile, status
from fastapi.responses import FileResponse
from starlette.background import BackgroundTask

from app.api.schemas import (
    EditBody,
    EditOut,
    FileEntryOut,
    FileListOut,
    FileStatOut,
    GlobBody,
    GlobOut,
    GrepBody,
    GrepOut,
    MoveCopyBody,
    MoveCopyOut,
    PathBody,
    PathOut,
    ReadBody,
    ReadOut,
    RenameBody,
    UploadOut,
    WriteBody,
)
from app.core import agentfs, beneath, fsops, spaces, vfs
from app.core.agentfs import AgentFsError
from app.core.beneath import Root
from app.core.errors import Conflict, Forbidden, InvalidInput, NotFound
from app.core.principal import CurrentUser, Principal

router = APIRouter(prefix="/files")

_CHUNK_SIZE = 1024 * 1024
_MISSING = (FileNotFoundError, NotADirectoryError)


async def resolve(
    request: Request, principal: Principal, path: str, need: spaces.Need, *, follow: bool = True
) -> vfs.Resolved:
    async with request.app.state.db_pool.connection() as conn:
        return await vfs.resolve_virtual_path(
            conn, principal, request.app.state.storage, path, need, follow=follow
        )


async def open_file(
    request: Request, principal: Principal, path: str, *, writable: bool = False
) -> tuple[vfs.Resolved, BinaryIO]:
    """A regular file, open (`writable` needs `write`), or `404 not_found`."""
    r = await resolve(request, principal, path, "write" if writable else "read")
    if r.kind != "space":
        raise NotFound("not_found")

    def _open() -> BinaryIO:
        with r.open_root() as root, _fs_errors():
            try:
                return fsops.open_regular(root, r.rel, writable=writable)
            except (*_MISSING, IsADirectoryError) as exc:
                raise NotFound("not_found") from exc

    return r, await anyio.to_thread.run_sync(_open)


def fd_path(f: BinaryIO) -> str:
    """A path that reopens exactly the file `f` has open, whatever its name now points at."""
    return f"/proc/self/fd/{f.fileno()}"


def _owner(principal: Principal, r: vfs.Resolved) -> fsops.Owner:
    return fsops.Owner(principal.uid, r.space["gid"])


@contextmanager
def _fs_errors() -> Iterator[None]:
    try:
        yield
    except IsADirectoryError as exc:
        raise Conflict("is_a_directory") from exc
    except NotADirectoryError as exc:
        raise Conflict("not_a_directory") from exc
    except FileExistsError as exc:
        raise Conflict("already_exists") from exc
    except FileNotFoundError as exc:
        raise NotFound("not_found") from exc
    except OSError as exc:
        # EXDEV: a symlink leading out of the space, planted after the path was checked.
        if exc.errno in (errno.ELOOP, errno.EXDEV):
            raise InvalidInput("invalid_path") from exc
        raise


def _mtime(ts: float) -> datetime:
    return datetime.fromtimestamp(ts, tz=UTC)


def _entry(vpath: str, name: str, st: os.stat_result) -> FileEntryOut:
    # From lstat: a symlink is listed as a file and never followed or sized through.
    is_dir = stat.S_ISDIR(st.st_mode)
    return FileEntryOut(
        name=name,
        path=vpath,
        type="dir" if is_dir else "file",
        size=0 if is_dir else st.st_size,
        mtime=_mtime(st.st_mtime),
        mime=None if is_dir else mimetypes.guess_type(name)[0],
    )


def _sorted(entries: list[FileEntryOut]) -> list[FileEntryOut]:
    return sorted(entries, key=lambda e: (e.type != "dir", e.name.lower()))


def _space_label(space: spaces.Row) -> str:
    return "Personal" if space["kind"] == "personal" else space["name"]


def _space_entry(storage, space: spaces.Row, role: str | None) -> FileEntryOut:
    personal = space["kind"] == "personal"
    try:
        with vfs.open_files_root(storage, space["id"]) as root:
            mtime = _mtime(os.fstat(root.fd).st_mtime)
    except (OSError, NotFound):
        mtime = space["created_at"]
    return FileEntryOut(
        name=vfs.PERSONAL if personal else space["slug"],
        path=vfs.space_prefix(space),
        type="dir",
        size=0,
        mtime=mtime,
        mime=None,
        label=_space_label(space),
        role=role,
    )


async def _synthetic_entries(request: Request, principal: Principal, kind: str):
    async with request.app.state.db_pool.connection() as conn:
        rows = await spaces.list_user_spaces(conn, principal.user_id, only=principal.space_scope)
    storage = request.app.state.storage
    shared = [_space_entry(storage, r, r["role"]) for r in rows if r["kind"] == "shared"]
    if kind == "spaces":
        return _sorted(shared)
    personal = [_space_entry(storage, r, r["role"]) for r in rows if r["kind"] == "personal"]
    newest = max((e.mtime for e in [*personal, *shared]), default=datetime.now(UTC))
    spaces_entry = FileEntryOut(
        name=vfs.SPACES, path=vfs.join(vfs.SPACES), type="dir", size=0, mtime=newest,
        mime=None, label="Shared spaces",
    )  # fmt: skip
    return _sorted([*personal, spaces_entry])


def _require_dir(root: Root, parts: tuple[str, ...]) -> None:
    try:
        is_dir = stat.S_ISDIR(beneath.stat_at(root, parts).st_mode)
    except _MISSING as exc:
        raise NotFound("not_found") from exc
    if not is_dir:
        raise NotFound("not_found")


def _list_space_dir(r: vfs.Resolved) -> list[FileEntryOut]:
    with r.open_root() as root, _fs_errors():
        try:
            fd = beneath.open(root, r.rel, os.O_RDONLY | os.O_DIRECTORY)
        except _MISSING as exc:
            raise NotFound("not_found") from exc
    try:
        entries = []
        with os.scandir(fd) as it:
            for child in it:
                try:
                    st = child.stat(follow_symlinks=False)
                except FileNotFoundError:
                    continue
                entries.append(_entry(r.child(child.name), child.name, st))
    finally:
        os.close(fd)
    return _sorted(entries)


# --- file manager ------------------------------------------------------------------


@router.get("", response_model=FileListOut)
async def list_dir(request: Request, principal: CurrentUser, path: str = "") -> FileListOut:
    r = await resolve(request, principal, path, "read")
    if r.kind != "space":
        entries = await _synthetic_entries(request, principal, r.kind)
        return FileListOut(path=r.vpath, entries=entries, role=None, writable=False)
    entries = await anyio.to_thread.run_sync(_list_space_dir, r)
    return FileListOut(
        path=r.vpath,
        entries=entries,
        role=r.role,
        writable=r.writable,
        space_label=_space_label(r.space),
    )


@router.get("/stat", response_model=FileStatOut)
async def stat_path(request: Request, principal: CurrentUser, path: str) -> FileStatOut:
    r = await resolve(request, principal, path, "read", follow=False)
    if r.kind != "space":
        name = "" if r.kind == "root" else vfs.SPACES
        entry = FileEntryOut(
            name=name, path=r.vpath, type="dir", size=0, mtime=datetime.now(UTC), mime=None
        )
        return FileStatOut(entry=entry, role=None, writable=False)
    if r.is_space_root:
        entry = _space_entry(request.app.state.storage, r.space, r.role)
    else:

        def _lstat() -> os.stat_result:
            with r.open_root() as root, _fs_errors():
                try:
                    return beneath.lstat_at(root, r.rel)
                except _MISSING as exc:
                    raise NotFound("not_found") from exc

        entry = _entry(r.vpath, r.rel[-1], await anyio.to_thread.run_sync(_lstat))
    return FileStatOut(entry=entry, role=r.role, writable=r.writable)


@router.post("/upload", status_code=status.HTTP_201_CREATED, response_model=UploadOut)
async def upload(
    request: Request,
    principal: CurrentUser,
    path: str = Form(""),
    file: list[UploadFile] = File(...),  # noqa: B008 - FastAPI's multipart idiom
) -> UploadOut:
    """Overwrites existing files. Chunks are written as they arrive (no full buffering)."""
    r = await resolve(request, principal, path, "write")
    owner = _owner(principal, r)
    uploaded = []
    with r.open_root() as root:
        with _fs_errors():
            _require_dir(root, r.rel)
        for part in file:
            # basename strips client-supplied directories; "." and ".." survive it.
            name = os.path.basename(part.filename or "")
            if not name or name in (".", ".."):
                raise InvalidInput("invalid_filename")
            with _fs_errors(), fsops.open_for_write(root, (*r.rel, name), owner) as f:
                while chunk := await part.read(_CHUNK_SIZE):
                    f.write(chunk)
            uploaded.append(r.child(name))
    return UploadOut(uploaded=uploaded)


@router.get("/download")
async def download(request: Request, principal: CurrentUser, path: str) -> FileResponse:
    r, f = await open_file(request, principal, path)
    return FileResponse(
        fd_path(f),
        filename=r.rel[-1],
        stat_result=os.fstat(f.fileno()),
        background=BackgroundTask(f.close),
    )


@router.post("/mkdir", status_code=status.HTTP_201_CREATED, response_model=PathOut)
async def mkdir(body: PathBody, request: Request, principal: CurrentUser) -> PathOut:
    """`mkdir -p`: parents are created, an existing directory is fine, a file in the way is 409."""
    r = await resolve(request, principal, body.path, "write")

    def _run() -> None:
        with r.open_root() as root, _fs_errors():
            fsops.make_dirs(root, r.rel, _owner(principal, r))

    await anyio.to_thread.run_sync(_run)
    return PathOut(path=r.vpath)


def _check_destination(
    src: vfs.Resolved, src_root: Root, dst: vfs.Resolved, dst_root: Root, *, moving: bool
) -> None:
    if dst.is_space_root or (moving and src.is_space_root):
        raise Forbidden("read_only")
    if moving and src.is_apps_folder(src_root):
        raise Forbidden("reserved")
    try:
        # A move takes a final symlink itself; a copy copies what it points at.
        src_st = (beneath.lstat_at if moving else beneath.stat_at)(src_root, src.rel)
    except _MISSING as exc:
        raise NotFound("not_found") from exc
    try:
        beneath.lstat_at(dst_root, dst.rel)
    except _MISSING:
        pass
    else:
        raise Conflict("already_exists")
    try:
        parent_is_dir = stat.S_ISDIR(beneath.stat_at(dst_root, dst.rel[:-1]).st_mode)
    except _MISSING as exc:
        raise NotFound("parent_not_found") from exc
    if not parent_is_dir:
        raise NotFound("parent_not_found")
    if stat.S_ISDIR(src_st.st_mode) and beneath.is_inside(dst_root, dst.rel[:-1], src_st):
        raise InvalidInput("invalid_destination")


async def _move(request: Request, principal: Principal, src_path: str, dst_path: str):
    src = await resolve(request, principal, src_path, "write", follow=False)
    dst = await resolve(request, principal, dst_path, "write", follow=False)
    cross_space = src.space["id"] != dst.space["id"]

    def _run() -> None:
        with src.open_root() as src_root, dst.open_root() as dst_root, _fs_errors():
            _check_destination(src, src_root, dst, dst_root, moving=True)
            try:
                fsops.move(
                    src_root, src.rel, dst_root, dst.rel,
                    gid=dst.space["gid"] if cross_space else None,
                )  # fmt: skip
            except OSError as exc:
                if exc.errno == errno.EINVAL:  # into its own subtree
                    raise InvalidInput("invalid_destination") from exc
                raise

    await anyio.to_thread.run_sync(_run)
    return MoveCopyOut(src=src.vpath, dst=dst.vpath)


@router.post("/move", response_model=MoveCopyOut)
async def move(body: MoveCopyBody, request: Request, principal: CurrentUser) -> MoveCopyOut:
    """Rename or move, within a space or across spaces; a moved symlink stays a symlink."""
    return await _move(request, principal, body.src, body.dst)


@router.post("/rename", response_model=MoveCopyOut)
async def rename(body: RenameBody, request: Request, principal: CurrentUser) -> MoveCopyOut:
    name = body.name.strip()
    if not name or name in (".", "..") or "/" in name or "\x00" in name:
        raise InvalidInput("invalid_name")
    parts = vfs.parse(body.path)
    return await _move(request, principal, body.path, vfs.join(*parts[:-1], name))


@router.post("/copy", response_model=MoveCopyOut)
async def copy(body: MoveCopyBody, request: Request, principal: CurrentUser) -> MoveCopyOut:
    """Directories are copied recursively; the copies belong to the caller and `dst`'s space."""
    src = await resolve(request, principal, body.src, "write")
    dst = await resolve(request, principal, body.dst, "write", follow=False)

    def _run() -> None:
        with src.open_root() as src_root, dst.open_root() as dst_root, _fs_errors():
            _check_destination(src, src_root, dst, dst_root, moving=False)
            fsops.copy(src_root, src.rel, dst_root, dst.rel, _owner(principal, dst))

    await anyio.to_thread.run_sync(_run)
    return MoveCopyOut(src=src.vpath, dst=dst.vpath)


@router.delete("", status_code=status.HTTP_204_NO_CONTENT)
async def delete(request: Request, principal: CurrentUser, path: str) -> None:
    """Directories are deleted recursively; a symlink is removed, never what it points at."""
    r = await resolve(request, principal, path, "write", follow=False)
    if r.is_space_root:
        raise Forbidden("read_only")

    def _run() -> None:
        with r.open_root() as root, _fs_errors():
            if r.is_apps_folder(root):
                raise Forbidden("reserved")
            try:
                fsops.remove(root, r.rel)
            except _MISSING as exc:
                raise NotFound("not_found") from exc

    await anyio.to_thread.run_sync(_run)


# --- agent file tools ----------------------------------------------------------------


@router.post("/read", response_model=ReadOut, response_model_exclude_none=True)
async def read(body: ReadBody, request: Request, principal: CurrentUser) -> ReadOut:
    r, f = await open_file(request, principal, body.path)
    with _fs_errors():
        result = await anyio.to_thread.run_sync(
            agentfs.read_file, f, r.vpath, body.offset, body.limit
        )
    return ReadOut(path=r.vpath, **result)


def _write_text(r: vfs.Resolved, owner: fsops.Owner, content: bytes) -> None:
    with r.open_root() as root, _fs_errors():
        fsops.make_dirs(root, r.rel[:-1], owner)
        with fsops.open_for_write(root, r.rel, owner) as f:
            f.write(content)


@router.post("/write", response_model=PathOut)
async def write(body: WriteBody, request: Request, principal: CurrentUser) -> PathOut:
    """Create or overwrite a text file, creating missing parent directories."""
    r = await resolve(request, principal, body.path, "write")
    if r.is_space_root:
        raise Conflict("is_a_directory")
    await anyio.to_thread.run_sync(
        _write_text, r, _owner(principal, r), body.content.encode("utf-8")
    )
    return PathOut(path=r.vpath)


@router.put("/content", response_model=PathOut)
async def put_content(request: Request, principal: CurrentUser, path: str) -> PathOut:
    """Raw request body -> file (create or overwrite, parents created): deepagents' upload_files."""
    r = await resolve(request, principal, path, "write")
    if r.is_space_root:
        raise Conflict("is_a_directory")
    owner = _owner(principal, r)
    with r.open_root() as root, _fs_errors():
        await anyio.to_thread.run_sync(fsops.make_dirs, root, r.rel[:-1], owner)
        with fsops.open_for_write(root, r.rel, owner) as f:
            async for chunk in request.stream():
                f.write(chunk)
    return PathOut(path=r.vpath)


def _edit(r: vfs.Resolved, f: BinaryIO, body: EditBody) -> agentfs.Edit:
    with f:
        raw = f.read()
        try:
            content = raw.decode("utf-8").replace("\r\n", "\n").replace("\r", "\n")
        except UnicodeDecodeError as exc:
            raise AgentFsError("not_text", f"Error editing file '{r.vpath}': {exc}") from exc
        result = agentfs.apply_edit(content, body.old_string, body.new_string, body.replace_all)
        f.seek(0)
        f.truncate()
        f.write(result.content.encode("utf-8"))
    return result


@router.post("/edit", response_model=EditOut, response_model_exclude_none=True)
async def edit(body: EditBody, request: Request, principal: CurrentUser) -> EditOut:
    r, f = await open_file(request, principal, body.path, writable=True)
    with _fs_errors():
        result = await anyio.to_thread.run_sync(_edit, r, f, body)
    return EditOut(path=r.vpath, occurrences=result.occurrences, note=result.note)


async def _trees(request: Request, principal: Principal, path: str | None):
    """(search base, trees): one tree inside a space, or every readable space for `/`/`/spaces`."""
    storage = request.app.state.storage
    r = await resolve(request, principal, path or "/", "read")
    if r.kind == "space":
        return r.vpath, [agentfs.Tree(storage, r.space["id"], r.rel, r.prefix)]
    async with request.app.state.db_pool.connection() as conn:
        rows = await spaces.list_user_spaces(conn, principal.user_id, only=principal.space_scope)
    trees = [
        agentfs.Tree(storage, row["id"], (), vfs.space_prefix(row))
        for row in rows
        if r.kind != "spaces" or row["kind"] == "shared"
    ]
    return r.vpath, trees


@router.post("/grep", response_model=GrepOut)
async def grep(body: GrepBody, request: Request, principal: CurrentUser) -> GrepOut:
    base, trees = await _trees(request, principal, body.path)
    outcome = await anyio.to_thread.run_sync(
        lambda: agentfs.grep(trees, base, body.pattern, body.glob, body.max_count)
    )
    return GrepOut(matches=outcome.matches, truncated=outcome.truncated, error=outcome.error)


@router.post("/glob", response_model=GlobOut)
async def glob(body: GlobBody, request: Request, principal: CurrentUser) -> GlobOut:
    agentfs.compile_glob(body.pattern)
    base, trees = await _trees(request, principal, body.path)
    matches, truncated = await anyio.to_thread.run_sync(agentfs.glob, trees, base, body.pattern)
    return GlobOut(
        matches=matches, truncated=truncated, truncation_reason="budget" if truncated else None
    )

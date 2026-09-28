"""`/api/platform/files*`: the files API over virtual paths (docs/PLATFORM.md §5).

Every `path`/`src`/`dst` goes through `vfs.resolve_virtual_path` (membership,
role, traversal, symlink escape) before the filesystem is touched; reads
need `read` (viewer), anything that changes a space needs `write` (editor),
a cross-space move/copy needs `write` on both. Files and directories created
here belong to the caller's uid and the space's gid (`app.core.fsops`).

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
from pathlib import Path

import anyio.to_thread
from fastapi import APIRouter, File, Form, Request, UploadFile, status
from fastapi.responses import FileResponse

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
from app.core import agentfs, fsops, spaces, vfs
from app.core.agentfs import AgentFsError
from app.core.errors import Conflict, Forbidden, InvalidInput, NotFound
from app.core.principal import CurrentUser, Principal

router = APIRouter(prefix="/files")

_CHUNK_SIZE = 1024 * 1024


async def resolve(
    request: Request, principal: Principal, path: str, need: spaces.Need, *, follow: bool = True
) -> vfs.Resolved:
    async with request.app.state.db_pool.connection() as conn:
        return await vfs.resolve_virtual_path(
            conn, principal, request.app.state.storage, path, need, follow=follow
        )


async def resolve_file(request: Request, principal: Principal, path: str) -> vfs.Resolved:
    """A readable regular file, or `404 not_found`."""
    r = await resolve(request, principal, path, "read")
    if r.kind != "space" or not r.host_path.is_file():
        raise NotFound("not_found")
    return r


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
        if exc.errno == errno.ELOOP:
            raise InvalidInput("invalid_path") from exc
        raise


def _mtime(ts: float) -> datetime:
    return datetime.fromtimestamp(ts, tz=UTC)


def _entry(vpath: str, host: Path, name: str | None = None) -> FileEntryOut:
    # lstat: a symlink is listed as a file and never followed or sized through.
    st = host.lstat()
    is_dir = stat.S_ISDIR(st.st_mode)
    name = name if name is not None else host.name
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
        mtime = _mtime(vfs.files_root(storage, space["id"]).stat().st_mtime)
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
        rows = await spaces.list_user_spaces(conn, principal.user_id)
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


def _list_space_dir(r: vfs.Resolved) -> list[FileEntryOut]:
    entries = []
    for child in r.host_path.iterdir():
        try:
            entries.append(_entry(r.child(child.name), child))
        except FileNotFoundError:
            continue
    return _sorted(entries)


# --- file manager ------------------------------------------------------------------


@router.get("", response_model=FileListOut)
async def list_dir(request: Request, principal: CurrentUser, path: str = "") -> FileListOut:
    r = await resolve(request, principal, path, "read")
    if r.kind != "space":
        entries = await _synthetic_entries(request, principal, r.kind)
        return FileListOut(path=r.vpath, entries=entries, role=None, writable=False)
    if not r.host_path.is_dir():
        raise NotFound("not_found")
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
    elif os.path.lexists(r.host_path):
        entry = _entry(r.vpath, r.host_path)
    else:
        raise NotFound("not_found")
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
    if not r.host_path.is_dir():
        raise NotFound("not_found")
    owner = _owner(principal, r)
    uploaded = []
    for part in file:
        # basename strips client-supplied directories; "." and ".." survive it.
        name = os.path.basename(part.filename or "")
        if not name or name in (".", ".."):
            raise InvalidInput("invalid_filename")
        dest = vfs.contain(r.files_root, str((r.host_path / name).relative_to(r.files_root)))
        with _fs_errors(), fsops.open_for_write(dest, owner) as f:
            while chunk := await part.read(_CHUNK_SIZE):
                f.write(chunk)
        uploaded.append(r.child(name))
    return UploadOut(uploaded=uploaded)


@router.get("/download")
async def download(request: Request, principal: CurrentUser, path: str) -> FileResponse:
    r = await resolve_file(request, principal, path)
    return FileResponse(r.host_path, filename=vfs.parse(r.vpath)[-1])


@router.post("/mkdir", status_code=status.HTTP_201_CREATED, response_model=PathOut)
async def mkdir(body: PathBody, request: Request, principal: CurrentUser) -> PathOut:
    """`mkdir -p`: parents are created, an existing directory is fine, a file in the way is 409."""
    r = await resolve(request, principal, body.path, "write")
    with _fs_errors():
        await anyio.to_thread.run_sync(
            fsops.make_dirs, r.files_root, r.host_path, _owner(principal, r)
        )
    return PathOut(path=r.vpath)


def _check_destination(src: vfs.Resolved, dst: vfs.Resolved, *, moving: bool) -> None:
    if dst.is_space_root or (moving and src.is_space_root):
        raise Forbidden("read_only")
    if moving and src.is_apps_folder:
        raise Forbidden("reserved")
    if not os.path.lexists(src.host_path):
        raise NotFound("not_found")
    if os.path.lexists(dst.host_path):
        raise Conflict("already_exists")
    if not dst.host_path.parent.is_dir():
        raise NotFound("parent_not_found")
    if dst.host_path == src.host_path or src.host_path in dst.host_path.parents:
        raise InvalidInput("invalid_destination")


async def _move(request: Request, principal: Principal, src_path: str, dst_path: str):
    src = await resolve(request, principal, src_path, "write", follow=False)
    dst = await resolve(request, principal, dst_path, "write", follow=False)
    _check_destination(src, dst, moving=True)

    def _run() -> None:
        os.rename(src.host_path, dst.host_path)
        if src.space["id"] != dst.space["id"]:
            fsops.adopt_tree(dst.host_path, dst.space["gid"])

    with _fs_errors():
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
    _check_destination(src, dst, moving=False)
    with _fs_errors():
        await anyio.to_thread.run_sync(
            fsops.copy, src.host_path, dst.host_path, _owner(principal, dst)
        )
    return MoveCopyOut(src=src.vpath, dst=dst.vpath)


@router.delete("", status_code=status.HTTP_204_NO_CONTENT)
async def delete(request: Request, principal: CurrentUser, path: str) -> None:
    """Directories are deleted recursively; a symlink is removed, never what it points at."""
    r = await resolve(request, principal, path, "write", follow=False)
    if r.is_space_root:
        raise Forbidden("read_only")
    if r.is_apps_folder:
        raise Forbidden("reserved")
    if not os.path.lexists(r.host_path):
        raise NotFound("not_found")
    with _fs_errors():
        await anyio.to_thread.run_sync(fsops.remove, r.host_path)


# --- agent file tools ----------------------------------------------------------------


@router.post("/read", response_model=ReadOut, response_model_exclude_none=True)
async def read(body: ReadBody, request: Request, principal: CurrentUser) -> ReadOut:
    r = await resolve_file(request, principal, body.path)
    with _fs_errors():
        result = await anyio.to_thread.run_sync(
            agentfs.read_file, r.host_path, r.vpath, body.offset, body.limit
        )
    return ReadOut(path=r.vpath, **result)


def _write_text(r: vfs.Resolved, owner: fsops.Owner, content: bytes) -> None:
    fsops.make_dirs(r.files_root, r.host_path.parent, owner)
    with fsops.open_for_write(r.host_path, owner) as f:
        f.write(content)


@router.post("/write", response_model=PathOut)
async def write(body: WriteBody, request: Request, principal: CurrentUser) -> PathOut:
    """Create or overwrite a text file, creating missing parent directories."""
    r = await resolve(request, principal, body.path, "write")
    if r.is_space_root:
        raise Conflict("is_a_directory")
    with _fs_errors():
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
    with _fs_errors():
        await anyio.to_thread.run_sync(fsops.make_dirs, r.files_root, r.host_path.parent, owner)
        with fsops.open_for_write(r.host_path, owner) as f:
            async for chunk in request.stream():
                f.write(chunk)
    return PathOut(path=r.vpath)


def _edit(r: vfs.Resolved, owner: fsops.Owner, body: EditBody) -> int:
    with fsops.open_regular(r.host_path) as f:
        raw = f.read()
    try:
        content = raw.decode("utf-8").replace("\r\n", "\n").replace("\r", "\n")
    except UnicodeDecodeError as exc:
        raise AgentFsError("not_text", f"Error editing file '{r.vpath}': {exc}") from exc
    new_content, occurrences = agentfs.edit_text(
        content, body.old_string, body.new_string, body.replace_all
    )
    with fsops.open_for_write(r.host_path, owner) as f:
        f.write(new_content.encode("utf-8"))
    return occurrences


@router.post("/edit", response_model=EditOut)
async def edit(body: EditBody, request: Request, principal: CurrentUser) -> EditOut:
    r = await resolve(request, principal, body.path, "write")
    if not r.host_path.is_file():
        raise NotFound("not_found")
    with _fs_errors():
        occurrences = await anyio.to_thread.run_sync(_edit, r, _owner(principal, r), body)
    return EditOut(path=r.vpath, occurrences=occurrences)


async def _trees(request: Request, principal: Principal, path: str | None):
    """(search base, trees): one tree inside a space, or every readable space for `/`/`/spaces`."""
    r = await resolve(request, principal, path or "/", "read")
    if r.kind == "space":
        if not os.path.lexists(r.host_path):
            return r.vpath, []
        return r.vpath, [agentfs.Tree(r.files_root, r.host_path, r.prefix)]
    async with request.app.state.db_pool.connection() as conn:
        rows = await spaces.list_user_spaces(conn, principal.user_id)
    trees = []
    for row in rows:
        if r.kind == "spaces" and row["kind"] != "shared":
            continue
        try:
            root = vfs.files_root(request.app.state.storage, row["id"])
        except NotFound:
            continue
        trees.append(agentfs.Tree(root, root, vfs.space_prefix(row)))
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

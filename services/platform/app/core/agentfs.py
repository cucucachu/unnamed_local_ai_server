"""Server side of the agent's file tools (docs/PLATFORM.md §6, M11-02's `PlatformFilesBackend`).

Mirrors `deepagents==0.7.11`'s `FilesystemBackend` so the agent backend can
be a thin HTTP client. The pieces below are ports of that version's
`deepagents.backends.utils` helpers (`slice_read_response`,
`perform_string_replacement`, `compile_grep_include_glob`, the read-type
extension map) and of `FilesystemBackend`'s Python grep fallback and glob
walk; keep them in step when deepagents is upgraded. Differences, all
deliberate:

- walks never follow symlinks, and grep/glob skip them (a link could lead
  out of the space);
- grep is always the Python search (no ripgrep, so no `.gitignore`
  filtering - the agent-server image has no `rg` either), results come
  in path order, and a grep on a file searches just that file (ripgrep's
  behaviour; the Python fallback walks the file's directory);
- timestamps are UTC ISO 8601 with an offset.

Line-number gutters are not added here: in 0.7.11 backends return raw text
and the filesystem middleware formats it.
"""

from __future__ import annotations

import base64
import functools
import io
import logging
import os
import stat
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath

import wcmatch.glob as wcglob

from app.core import fsops

logger = logging.getLogger(__name__)

MAX_VIDEO_INPUT_BYTES = 1024 * 1024 * 1024
GREP_TIMEOUT_S = 15
GREP_MAX_FILE_BYTES = 10 * 1024 * 1024
GLOB_TIMEOUT_S = 5
EMPTY_CONTENT_WARNING = "System reminder: File exists but has empty contents"

_BINARY_TYPES = {
    **dict.fromkeys((".png", ".jpeg", ".jpg", ".webp", ".gif", ".heic", ".heif"), "image"),
    **dict.fromkeys(
        (".mp4", ".mpeg", ".mov", ".avi", ".flv", ".mpg", ".webm", ".wmv", ".3gpp", ".mkv"),
        "video",
    ),
    **dict.fromkeys((".wav", ".mp3", ".aiff", ".aac", ".ogg", ".flac"), "audio"),
    **dict.fromkeys((".pdf", ".ppt", ".pptx"), "file"),
}


class AgentFsError(Exception):
    """A failure the agent should read: `code` for the API, `message` in deepagents' words."""

    def __init__(self, code: str, message: str, **extra) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.extra = extra


def read_type(name: str) -> str:
    """`text` or the multimodal type deepagents reads as base64 (`image`, `video`, ...)."""
    return _BINARY_TYPES.get(PurePosixPath(name).suffix.lower(), "text")


# --- read ------------------------------------------------------------------------


def read_file(host: Path, vpath: str, offset: int, limit: int) -> dict:
    """A `ReadResult`-shaped dict: `content`, `encoding`, and the pagination fields."""
    kind = read_type(host.name)
    with fsops.open_regular(host) as f:
        if kind != "text":
            if kind == "video" and os.fstat(f.fileno()).st_size > MAX_VIDEO_INPUT_BYTES:
                raise AgentFsError(
                    "file_too_large",
                    f"Video file exceeds maximum input size of {MAX_VIDEO_INPUT_BYTES} bytes",
                )
            return {"content": base64.standard_b64encode(f.read()).decode("ascii"),
                    "encoding": "base64"}  # fmt: skip
        raw = f.read()
    try:
        # Universal newlines, like FilesystemBackend's text-mode open.
        content = raw.decode("utf-8").replace("\r\n", "\n").replace("\r", "\n")
    except UnicodeDecodeError as exc:
        raise AgentFsError("not_text", f"Error reading file '{vpath}': {exc}") from exc
    return slice_text(content, offset, limit)


def slice_text(content: str, offset: int, limit: int) -> dict:
    offset, limit = max(int(offset), 0), max(int(limit), 0)
    if not content or content.strip() == "":
        return {"content": EMPTY_CONTENT_WARNING, "encoding": "utf-8"}
    if limit == 0:
        return {"content": "", "encoding": "utf-8", "no_lines_requested": True}

    lines = content.splitlines(keepends=True)
    total = len(lines)
    if offset >= total:
        raise AgentFsError(
            "offset_out_of_range",
            f"Line offset {offset} exceeds file length ({total} lines)",
            total_lines=total,
        )
    end = min(offset + limit, total)
    return {
        "content": "".join(lines[offset:end]),
        "encoding": "utf-8",
        "total_lines": total,
        "start_line": offset + 1,
        "end_line": end,
        "next_offset": end if end < total else None,
    }


# --- edit ------------------------------------------------------------------------


def replace_string(content: str, old: str, new: str, replace_all: bool) -> tuple[str, int]:
    occurrences = content.count(old)
    if occurrences == 0:
        if old.endswith("\n") and len(old) > 1 and content.endswith(old.removesuffix("\n")):
            stripped_count = content.count(old.removesuffix("\n"))
            if stripped_count == 1:
                raise AgentFsError(
                    "trailing_newline_mismatch",
                    "Error: old_string ends with a newline, but the file does not end with a "
                    "newline. Retry with the trailing newline removed from old_string (and from "
                    "new_string if it also ends with a newline).",
                )
            raise AgentFsError(
                "trailing_newline_mismatch",
                "Error: old_string ends with a newline, but the file does not end with a "
                f"newline. With the trailing newline removed, old_string would appear "
                f"{stripped_count} times in the file. Retry with the trailing newline removed "
                "and add surrounding context so the match is unique.",
            )
        raise AgentFsError("string_not_found", f"Error: String not found in file: '{old}'")
    if occurrences > 1 and not replace_all:
        raise AgentFsError(
            "string_not_unique",
            f"Error: String '{old}' appears {occurrences} times in file. Use replace_all=True "
            "to replace all instances, or provide a more specific string with surrounding "
            "context.",
            occurrences=occurrences,
        )
    return content.replace(old, new), occurrences


def edit_text(content: str, old: str, new: str, replace_all: bool) -> tuple[str, int]:
    old = old.replace("\r\n", "\n").replace("\r", "\n")
    new = new.replace("\r\n", "\n").replace("\r", "\n")
    return replace_string(content, old, new, replace_all)


# --- grep / glob -----------------------------------------------------------------


@functools.lru_cache(maxsize=256)
def compile_glob(pattern: str) -> Callable[[str], bool]:
    """Predicate on a search-root-relative POSIX path (deepagents' shared glob contract)."""
    if ".." in pattern.replace("\\", "/").split("/"):
        raise AgentFsError(
            "invalid_glob", f"Path traversal not allowed in glob pattern {pattern!r}"
        )
    anchored = "/" in pattern
    try:
        compiled = wcglob.compile(pattern.lstrip("/"), flags=wcglob.BRACE | wcglob.GLOBSTAR)
    except Exception as exc:
        raise AgentFsError("invalid_glob", f"Invalid glob pattern {pattern!r}: {exc}") from exc
    if anchored:
        return lambda rel: bool(compiled.match(rel))
    return lambda rel: bool(compiled.match(PurePosixPath(rel).name))


@dataclass(frozen=True)
class Tree:
    """One place to search: `start` (a dir or a file) inside a space's `files_root`."""

    files_root: Path
    start: Path
    # Virtual path of `files_root` (`/personal`, `/spaces/<slug>`).
    prefix: str

    def vpath(self, host: Path) -> str:
        if host == self.files_root:
            return self.prefix
        return f"{self.prefix}/{host.relative_to(self.files_root).as_posix()}"


def _rel(vpath: str, base: str) -> str:
    """`vpath` relative to the search path `base` (both virtual)."""
    base = base.rstrip("/")
    return vpath[len(base) :].lstrip("/")


def _regular_files(start: Path) -> Iterator[Path]:
    try:
        st = os.lstat(start)
    except OSError:
        return
    if stat.S_ISREG(st.st_mode):
        yield start
        return
    if not stat.S_ISDIR(st.st_mode):
        return
    for dirpath, dirnames, filenames in os.walk(start):
        dirnames.sort()
        for name in sorted(filenames):
            p = Path(dirpath) / name
            try:
                if stat.S_ISREG(os.lstat(p).st_mode):
                    yield p
            except OSError:
                continue


def _base_for(tree: Tree, base: str) -> str:
    """Glob-relative base: a single-file search matches against the file's name."""
    if tree.start.is_file() and not tree.start.is_symlink():
        return tree.vpath(tree.start.parent)
    return base


@dataclass
class GrepOutcome:
    matches: list[dict] = field(default_factory=list)
    truncated: bool = False
    error: str | None = None


def grep(
    trees: list[Tree],
    base: str,
    pattern: str,
    glob: str | None,
    max_count: int | None,
    *,
    timeout_s: float = GREP_TIMEOUT_S,
) -> GrepOutcome:
    """Literal substring search, line by line; `max_count` caps matches across all files."""
    matcher = compile_glob(glob) if glob else None
    deadline = time.monotonic() + timeout_s
    out = GrepOutcome()
    file_errors: list[str] = []

    def finish(truncated: bool, *, timed_out: bool = False) -> GrepOutcome:
        out.truncated = truncated
        if file_errors and not timed_out:
            out.error = "One or more files could not be fully searched:\n" + "\n".join(file_errors)
        return out

    for tree in trees:
        rel_base = _base_for(tree, base)
        for path in _regular_files(tree.start):
            if time.monotonic() > deadline:
                return finish(True, timed_out=True)
            vpath = tree.vpath(path)
            if matcher is not None and not matcher(_rel(vpath, rel_base)):
                continue
            try:
                if path.stat().st_size > GREP_MAX_FILE_BYTES:
                    continue
            except OSError:
                continue
            scanned = 0
            try:
                with io.TextIOWrapper(fsops.open_regular(path), encoding="utf-8") as handle:
                    for line_no, line in enumerate(handle, 1):
                        scanned = line_no
                        if line_no % 2048 == 0 and time.monotonic() > deadline:
                            return finish(True, timed_out=True)
                        if pattern not in line:
                            continue
                        if max_count is not None and len(out.matches) >= max_count:
                            return finish(True)
                        out.matches.append(
                            {"path": vpath, "line": line_no, "text": line.rstrip("\n")}
                        )
            except UnicodeDecodeError as exc:
                if scanned:
                    file_errors.append(f"- {vpath}: UnicodeDecodeError: {exc.reason}")
            except OSError as exc:
                file_errors.append(f"- {vpath}: {type(exc).__name__}: {exc.strerror}")
    return finish(False)


def _file_info(vpath: str, path: Path) -> dict:
    info: dict = {"path": vpath, "is_dir": False}
    try:
        st = os.lstat(path)
    except OSError:
        return info
    info["size"] = st.st_size
    info["modified_at"] = datetime.fromtimestamp(st.st_mtime, tz=UTC).isoformat()
    return info


def glob(
    trees: list[Tree], base: str, pattern: str, *, timeout_s: float = GLOB_TIMEOUT_S
) -> tuple[list[dict], bool]:
    """Regular files matching `pattern` relative to `base`, sorted by path; (matches, truncated)."""
    matcher = compile_glob(pattern)
    deadline = time.monotonic() + timeout_s
    results: list[dict] = []
    truncated = False
    for tree in trees:
        if not tree.start.is_dir() or tree.start.is_symlink():
            continue
        for path in _regular_files(tree.start):
            if time.monotonic() > deadline:
                truncated = True
                break
            vpath = tree.vpath(path)
            if matcher(_rel(vpath, base)):
                results.append(_file_info(vpath, path))
        if truncated:
            break
    results.sort(key=lambda r: r["path"])
    return results, truncated

"""App source history: one platform-owned git repo per app (docs/PLATFORM.md §7 "Source history").

    <platform data>/app-git/                root 0700
    <platform data>/app-git/.home/          root 0700, empty: git's HOME
    <platform data>/app-git/<app_id>.git/   a bare repo, branch `main`

A successful build commits the staged copy of the source it built
(`appbuild.Builds.stage`), never the source folder: that folder is writable
by the space's members and their exec containers, so git never runs in it
and there is no `.git` there for the platform to trust. A `.git`, `.gitattributes`
or anything else a user puts in the folder is just a dotfile the staging copy
skips.

Every git call is hardened the same way: an empty environment apart from the
variables set here (no inherited `GIT_*`), no system or global config, an
empty platform-owned `HOME`, hooks and fsmonitor off, no attributes or
excludes files, no network protocols, and `safe.directory` naming just the
repo. The work tree, when there is one, is the platform-owned staging dir.

Commits carry trailers (`Version`, `User`, `Thread`, `Reverts`) that
`log` parses back; only the platform writes them. A build whose tree equals
the branch head's adds no commit and records the head.
"""

from __future__ import annotations

import logging
import os
import re
import secrets
import shutil
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

import anyio.to_thread
from psycopg_pool import AsyncConnectionPool

from app.core import apps, spaces
from app.core.errors import ServerError
from app.core.principal import Principal

logger = logging.getLogger(__name__)

HISTORY_DIR = "app-git"
BRANCH = "refs/heads/main"
GIT_TIMEOUT_S = 60.0
MAX_LOG_LIMIT = 100

COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
_REV_RE = re.compile(r"^[0-9a-f]{7,40}$")
_TRAILER_RE = re.compile(r"^(Version|User|Thread|Reverts): (.+)$")
_UNSAFE = re.compile(r"[\x00-\x1f\x7f]")

_IDENTITY = {
    "GIT_AUTHOR_NAME": "Home AI",
    "GIT_AUTHOR_EMAIL": "platform@homeai.invalid",
    "GIT_COMMITTER_NAME": "Home AI",
    "GIT_COMMITTER_EMAIL": "platform@homeai.invalid",
}
# `-c` is protected config: it's the only place `safe.directory` counts, and
# it overrides anything the repo's own config could say.
_HARDENING = (
    "core.hooksPath=/dev/null",
    "core.fsmonitor=false",
    "core.attributesFile=/dev/null",
    "core.excludesFile=/dev/null",
    "core.untrackedCache=false",
    "core.symlinks=false",
    "core.autocrlf=false",
    "core.sshCommand=false",
    "protocol.allow=never",
    "commit.gpgSign=false",
    "gc.auto=0",
    "maintenance.auto=false",
)


class HistoryError(Exception):
    """A git call failed; the message is for logs (no user paths or content)."""


@dataclass(frozen=True)
class Commit:
    id: str
    parent: str | None
    subject: str
    version: str | None
    user: str | None
    thread_id: str | None
    reverts: str | None
    created_at: datetime

    @property
    def kind(self) -> str:
        return "revert" if self.reverts else "build"


def _clean(value: str, limit: int = 200) -> str:
    return _UNSAFE.sub("", value)[:limit]


def message(
    subject: str, *, version: str, user: str, thread_id: str | None = None,
    reverts: str | None = None,
) -> str:  # fmt: skip
    """A commit message: `subject`, then the trailers `log` reads back."""
    lines = [f"Version: {_clean(version, 64)}", f"User: {_clean(user, 64)}"]
    if thread_id:
        lines.append(f"Thread: {_clean(thread_id, 64)}")
    if reverts:
        lines.append(f"Reverts: {reverts}")
    return f"{_clean(subject)}\n\n" + "\n".join(lines) + "\n"


def _parse(record: str) -> Commit:
    commit_id, parents, stamp, body = record.split("\x1f", 3)
    lines = body.strip("\n").split("\n")
    trailers: dict[str, str] = {}
    for line in reversed(lines):
        match = _TRAILER_RE.fullmatch(line)
        if match is None:
            break
        trailers.setdefault(match[1], match[2])
    reverts = trailers.get("Reverts")
    return Commit(
        id=commit_id,
        parent=parents.split(" ")[0] if parents else None,
        subject=lines[0] if lines else "",
        version=trailers.get("Version"),
        user=trailers.get("User"),
        thread_id=trailers.get("Thread"),
        reverts=reverts if reverts and COMMIT_RE.fullmatch(reverts) else None,
        created_at=datetime.fromtimestamp(int(stamp), UTC),
    )


class AppHistory:
    """The repos under `root` (`<platform data>/app-git`)."""

    def __init__(self, root: Path, git: str = "git") -> None:
        self.root = root
        self.home = root / ".home"
        self.git = git

    def prepare(self) -> None:
        """Make the root and git's HOME platform-only; raise if git isn't installed."""
        found = shutil.which(self.git)
        if found is None:
            raise HistoryError(f"no {self.git} binary")
        self.git = found
        for path in (self.root, self.home):
            path.mkdir(mode=0o700, exist_ok=True)
            os.chmod(path, 0o700, follow_symlinks=False)

    def repo(self, app_id: UUID) -> Path:
        return self.root / f"{UUID(str(app_id))}.git"

    # --- running git -------------------------------------------------------------

    def _run(
        self, repo: Path, *args: str, stdin: bytes | None = None, work_tree: Path | None = None,
        index: Path | None = None, check: bool = True,
    ) -> subprocess.CompletedProcess[bytes]:  # fmt: skip
        env = {
            "PATH": "/usr/local/bin:/usr/bin:/bin",
            "HOME": str(self.home),
            "XDG_CONFIG_HOME": str(self.home),
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": "/dev/null",
            "GIT_ATTR_NOSYSTEM": "1",
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_NO_REPLACE_OBJECTS": "1",
            "GIT_ALLOW_PROTOCOL": "",
            "GIT_DIR": str(repo),
            "LC_ALL": "C",
            "TZ": "UTC",
            **_IDENTITY,
        }
        if work_tree is not None:
            env["GIT_WORK_TREE"] = str(work_tree)
        if index is not None:
            env["GIT_INDEX_FILE"] = str(index)
        config = [arg for item in (*_HARDENING, f"safe.directory={repo}") for arg in ("-c", item)]
        try:
            done = subprocess.run(
                [self.git, *config, *args],
                input=stdin,
                capture_output=True,
                env=env,
                cwd=work_tree or self.home,
                timeout=GIT_TIMEOUT_S,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise HistoryError(f"git {args[0]}: {type(exc).__name__}") from exc
        if check and done.returncode != 0:
            detail = done.stderr.decode("utf-8", "replace").strip().splitlines()[-1:] or [""]
            raise HistoryError(f"git {args[0]} exited {done.returncode}: {detail[0][:200]}")
        return done

    def _ensure(self, app_id: UUID) -> Path:
        repo = self.repo(app_id)
        if not (repo / "HEAD").is_file():
            repo.mkdir(mode=0o700, exist_ok=True)
            self._run(repo, "init", "-q", "--bare", "--template=", "--initial-branch=main",
                      str(repo))  # fmt: skip
        return repo

    def _head(self, repo: Path) -> str | None:
        done = self._run(repo, "rev-parse", "-q", "--verify", f"{BRANCH}^{{commit}}", check=False)
        head = done.stdout.decode().strip()
        return head if done.returncode == 0 and COMMIT_RE.fullmatch(head) else None

    def _tree_of(self, repo: Path, commit: str) -> str:
        out = self._run(repo, "rev-parse", "--verify", f"{commit}^{{tree}}").stdout
        return out.decode().strip()

    # --- writing -------------------------------------------------------------------

    def write_tree(self, app_id: UUID, work_tree: Path) -> str:
        """Store every file under `work_tree` (a platform-owned staging dir); returns the tree id."""
        repo = self._ensure(app_id)
        index = repo / f"index-{secrets.token_hex(8)}"
        try:
            self._run(repo, "add", "-A", "--", ".", work_tree=work_tree, index=index)
            return self._run(repo, "write-tree", index=index).stdout.decode().strip()
        finally:
            index.unlink(missing_ok=True)

    def commit(self, app_id: UUID, tree: str, text: str) -> tuple[str, bool]:
        """(the branch head, whether it is new): a commit of `tree` on top of the head, if it differs.

        The caller serializes commits to one app (the working version's row lock).
        """
        repo = self._ensure(app_id)
        head = self._head(repo)
        if head is not None and self._tree_of(repo, head) == tree:
            return head, False
        parent = ("-p", head) if head else ()
        new = self._run(repo, "commit-tree", tree, *parent, stdin=text.encode()).stdout
        new_id = new.decode().strip()
        self._run(repo, "update-ref", BRANCH, new_id, head or "0" * 40)
        return new_id, True

    def commit_revert(self, app_id: UUID, target: str, text: str) -> tuple[str, bool]:
        """`commit` of `target`'s tree (a commit `resolve` returned)."""
        repo = self._ensure(app_id)
        return self.commit(app_id, self._tree_of(repo, target), text)

    def tree_of(self, app_id: UUID, commit: str) -> str | None:
        """The tree id of `commit`, or None if the app has no history or no such commit."""
        repo = self.repo(app_id)
        if not COMMIT_RE.fullmatch(commit) or not (repo / "HEAD").is_file():
            return None
        try:
            return self._tree_of(repo, commit)
        except HistoryError:
            return None

    # --- reading -------------------------------------------------------------------

    def resolve(self, app_id: UUID, rev: str) -> str | None:
        """The full id of commit `rev` (hex, 7-40 chars) if it's on the app's branch."""
        repo = self.repo(app_id)
        if not _REV_RE.fullmatch(rev) or not (repo / "HEAD").is_file():
            return None
        done = self._run(repo, "rev-parse", "-q", "--verify", f"{rev}^{{commit}}", check=False)
        commit = done.stdout.decode().strip()
        if done.returncode != 0 or not COMMIT_RE.fullmatch(commit):
            return None
        head = self._head(repo)
        if head is None:
            return None
        ancestor = self._run(repo, "merge-base", "--is-ancestor", commit, head, check=False)
        return commit if ancestor.returncode == 0 else None

    def log(self, app_id: UUID, offset: int, limit: int) -> tuple[list[Commit], bool]:
        """Up to `limit` commits from the head back, skipping `offset`; and whether there are more."""
        repo = self.repo(app_id)
        if not (repo / "HEAD").is_file() or self._head(repo) is None:
            return [], False
        limit = max(1, min(limit, MAX_LOG_LIMIT))
        out = self._run(
            repo, "log", "--first-parent", f"--skip={offset}", f"--max-count={limit + 1}",
            "--format=%H%x1f%P%x1f%ct%x1f%B%x1e", BRANCH, "--",
        ).stdout.decode("utf-8", "replace")  # fmt: skip
        commits = [_parse(r.lstrip("\n")) for r in out.split("\x1e") if r.strip()]
        return commits[:limit], len(commits) > limit

    def get(self, app_id: UUID, commit: str) -> Commit:
        repo = self.repo(app_id)
        out = self._run(repo, "log", "-1", "--format=%H%x1f%P%x1f%ct%x1f%B", commit, "--").stdout
        return _parse(out.decode("utf-8", "replace"))

    def read_tree(
        self, app_id: UUID, commit: str, max_entries: int, max_bytes: int
    ) -> dict[str, bytes]:
        """{path: content} of every file in `commit`, within the staging copy's limits."""
        repo = self.repo(app_id)
        listing = self._run(repo, "ls-tree", "-r", "-z", "--full-tree", commit).stdout
        entries: list[tuple[str, str]] = []
        for raw in filter(None, listing.split(b"\x00")):
            meta, _, path = raw.partition(b"\t")
            mode, kind, oid = meta.decode().split(" ")
            if kind != "blob" or mode not in ("100644", "100755"):
                raise HistoryError(f"unexpected {kind} {mode} in {commit}")
            entries.append((path.decode("utf-8"), oid))
        if len(entries) > max_entries:
            raise HistoryError(f"{commit} has more than {max_entries} files")
        sizes = self._run(repo, "cat-file", "--batch-check=%(objectsize)",
                          stdin="".join(f"{oid}\n" for _, oid in entries).encode())  # fmt: skip
        if sum(int(n) for n in sizes.stdout.split()) > max_bytes:
            raise HistoryError(f"{commit} is larger than {max_bytes} bytes")
        out = self._run(repo, "cat-file", "--batch",
                        stdin="".join(f"{oid}\n" for _, oid in entries).encode()).stdout  # fmt: skip
        files: dict[str, bytes] = {}
        pos = 0
        for path, oid in entries:
            end = out.index(b"\n", pos)
            got, kind, size = out[pos:end].decode().split(" ")
            if (got, kind) != (oid, "blob"):
                raise HistoryError(f"cat-file answered {kind} {got} for {oid}")
            files[path] = out[end + 1 : end + 1 + int(size)]
            pos = end + 2 + int(size)
        return files


async def list_history(
    pool: AsyncConnectionPool,
    principal: Principal,
    history: AppHistory,
    app_id: UUID,
    offset: int,
    limit: int,
) -> tuple[list[dict[str, Any]], int | None]:
    """(commits newest first, the next page's offset or None). Needs `read` on the source space."""
    async with pool.connection() as conn:
        app = await apps.get_visible_app(conn, principal, app_id)
        await spaces.authorize_space(conn, principal, app["source_space_id"], "read")
    try:
        commits, more = await anyio.to_thread.run_sync(history.log, app_id, offset, limit)
    except HistoryError as exc:
        logger.error("history: app %s: log failed: %s", app_id, exc)
        raise ServerError("history_failed") from exc
    working = app["working_version"]
    current = working["commit"] if working else None
    return [as_dict(c, current) for c in commits], offset + len(commits) if more else None


def as_dict(commit: Commit, current: str | None) -> dict[str, Any]:
    return {
        "id": commit.id,
        "parent": commit.parent,
        "kind": commit.kind,
        "subject": commit.subject,
        "version": commit.version,
        "user": commit.user,
        "thread_id": commit.thread_id,
        "reverts": commit.reverts,
        "created_at": commit.created_at,
        "current": commit.id == current,
    }

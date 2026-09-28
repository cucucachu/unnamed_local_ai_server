"""App packages: the `app.json` JSON Schema and the package layout check (docs/PLATFORM.md §7).

    <slug>/
      app.json        Expo-style {"name", "slug", "version", "homeai": {...}}
      AGENT.md        required
      schema.sql      required (may be empty)
      actions/*.sql   optional; `^[a-z][a-zA-Z0-9_]*\\.sql$`, no subfolders
      app/            `_layout.tsx` + `index.tsx` required; route rules below

Image-shipped system apps (M14-03, `validate_package(..., shipped=True)`):
same files except `app/` is omitted (they render natively) and `actions/`
may hold `.json` descriptors for platform-implemented privileged actions.
`homeai.permissions.privileged` is only valid on a shipped package.

`validate_package(pkg_fd, slug)` returns every problem it finds as a
model-readable `Diagnostic` - `file` relative to the package, `path` a JSON
pointer into that file (`""` for non-JSON files or the file as a whole),
`message` a sentence saying what to change. Content (imports, types, SQL)
is the builder's job (M12-04), not this module's.

Routes (§7 "Routes"): only `.ts`/`.tsx` files under `app/`, segments
`name`, `index`, `[param]` and a final `[...rest]`, and the one root
`app/_layout.tsx`. Groups, nested layouts and `+special` files aren't in
the router shim yet, so they're diagnostics. Dotfiles are ignored and
symlinks are refused, never followed: the package is read below an open
fd on its folder, each directory opened `O_NOFOLLOW` from its parent's fd.
"""

from __future__ import annotations

import json
import os
import re
import stat
from dataclasses import asdict, dataclass
from pathlib import PurePosixPath
from typing import Any

from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError

from app.core import beneath, fsops
from app.core.beneath import Root

SDK_VERSIONS = ("1",)
MANIFEST_FILE = "app.json"
MAX_MANIFEST_BYTES = 256 * 1024
MAX_PACKAGE_ENTRIES = 1000
MAX_DIAGNOSTICS = 50

SLUG_PATTERN = r"^[a-z0-9][a-z0-9-]{0,39}$"
ACTION_RE = re.compile(r"^[a-z][a-zA-Z0-9_]*\.sql$")
ACTION_JSON_RE = re.compile(r"^[a-z][a-zA-Z0-9_]*\.json$")
# Image-shipped system apps (D17). User packages may not use these slugs or
# declare `homeai.permissions.privileged`.
SYSTEM_APP_SLUGS = ("home", "chat", "files", "settings")
PRIVILEGED_CAPABILITIES = ("files",)
NAME_SEGMENT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")
PARAM_SEGMENT_RE = re.compile(r"^\[[A-Za-z_][A-Za-z0-9_]*\]$")
REST_SEGMENT_RE = re.compile(r"^\[\.\.\.[A-Za-z_][A-Za-z0-9_]*\]$")
ROUTE_SUFFIXES = (".tsx", ".ts")

_RESERVED = (
    "reserved for cross-app data sharing, which isn't available yet: omit it or leave it empty ([])"
)

# `errorMessage` (the ajv-errors keyword) replaces the generic message for
# that node's own errors; validators ignore it.
SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": "https://homeai.local/api/platform/apps/schema",
    "title": "Home AI app manifest (app.json)",
    "description": (
        "Expo-style app.json. Top-level keys other than the ones below are allowed "
        "and ignored; the homeai block is strict."
    ),
    "type": "object",
    "required": ["name", "slug", "version", "homeai"],
    "properties": {
        "name": {
            "description": "Display name.",
            "type": "string",
            "minLength": 1,
            "maxLength": 64,
            "errorMessage": "name must be a string of 1-64 characters",
        },
        "slug": {
            "description": "Must equal the app's folder name: /<space>/Apps/<slug>/.",
            "type": "string",
            "pattern": SLUG_PATTERN,
            "errorMessage": (
                "slug must be 1-40 characters of lowercase letters, digits and '-', "
                "starting with a letter or digit"
            ),
        },
        "version": {
            "description": "Semantic version of the app, e.g. 1.0.0.",
            "type": "string",
            "pattern": (
                r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)"
                r"(-[0-9A-Za-z.-]+)?(\+[0-9A-Za-z.-]+)?$"
            ),
            "errorMessage": 'version must be a semantic version string like "1.0.0"',
        },
        "homeai": {
            "type": "object",
            "required": ["sdk", "icon"],
            "additionalProperties": False,
            "properties": {
                "sdk": {
                    "description": "@homeai/sdk version the app is written against.",
                    "type": "string",
                    "enum": list(SDK_VERSIONS),
                    "errorMessage": "sdk must be one of: "
                    + ", ".join(f'"{v}"' for v in SDK_VERSIONS),
                },
                "icon": {
                    "description": "Vector icon name (Ionicons style), e.g. cart-outline.",
                    "type": "string",
                    "maxLength": 64,
                    "pattern": r"^[a-z0-9]+(-[a-z0-9]+)*$",
                    "errorMessage": (
                        'icon must be a vector icon name like "cart-outline" '
                        "(lowercase words joined by '-')"
                    ),
                },
                "description": {
                    "description": "One or two sentences for the launcher and the agent.",
                    "type": "string",
                    "maxLength": 500,
                    "errorMessage": "description must be a string of at most 500 characters",
                },
                "permissions": {
                    "description": "Capabilities the app asks for. SDK 1 defines none yet.",
                    "type": "object",
                    "properties": {},
                    "additionalProperties": False,
                    "errorMessage": (
                        "SDK 1 defines no permissions yet: leave permissions empty ({}) or omit it"
                    ),
                },
                "exports": {"type": "array", "maxItems": 0, "errorMessage": _RESERVED},
                "reads": {"type": "array", "maxItems": 0, "errorMessage": _RESERVED},
            },
        },
    },
}

_VALIDATOR = Draft202012Validator(SCHEMA)

# Same schema, plus `permissions.privileged` for image-shipped system apps.
# `GET /apps/schema` keeps serving SCHEMA so user apps (and the authoring
# model) never see privileged as something they can declare.
SHIPPED_SCHEMA = json.loads(json.dumps(SCHEMA))
SHIPPED_SCHEMA["properties"]["homeai"]["properties"]["permissions"] = {
    "description": "Privileged capabilities only image-shipped system apps may hold.",
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "privileged": {
            "description": 'Platform-implemented action families. SDK 1: "files".',
            "type": "array",
            "items": {"type": "string", "enum": list(PRIVILEGED_CAPABILITIES)},
            "uniqueItems": True,
            "errorMessage": 'privileged must be an array of known names (SDK 1: "files")',
        },
    },
    "errorMessage": ('permissions may only include "privileged" on image-shipped system apps'),
}
_SHIPPED_VALIDATOR = Draft202012Validator(SHIPPED_SCHEMA)


@dataclass(frozen=True)
class Diagnostic:
    file: str
    path: str
    message: str

    def as_dict(self) -> dict[str, str]:
        return asdict(self)


def _pointer(parts) -> str:
    return "".join("/" + str(p).replace("~", "~0").replace("/", "~1") for p in parts)


def _schema_diagnostics(error: ValidationError) -> list[Diagnostic]:
    at = list(error.absolute_path)
    custom = error.schema.get("errorMessage") if isinstance(error.schema, dict) else None
    if error.validator == "required":
        return [
            Diagnostic(MANIFEST_FILE, _pointer([*at, key]), f'missing required property "{key}"')
            for key in error.validator_value
            if key not in error.instance
        ]
    if error.validator == "additionalProperties":
        known = error.schema.get("properties", {})
        allowed = ", ".join(known) or "none"
        return [
            Diagnostic(
                MANIFEST_FILE,
                _pointer([*at, key]),
                custom or f'unknown property "{key}" (allowed here: {allowed})',
            )
            for key in error.instance
            if key not in known
        ]
    return [Diagnostic(MANIFEST_FILE, _pointer(at), custom or error.message)]


def validate_manifest(doc: Any, *, shipped: bool = False) -> list[Diagnostic]:
    """Schema diagnostics for a parsed `app.json`, in document order."""
    validator = _SHIPPED_VALIDATOR if shipped else _VALIDATOR
    out: list[Diagnostic] = []
    seen: set[tuple[str, str]] = set()
    for error in sorted(validator.iter_errors(doc), key=lambda e: list(map(str, e.path))):
        for d in _schema_diagnostics(error):
            if (d.path, d.message) not in seen:
                seen.add((d.path, d.message))
                out.append(d)
    return out


_OPEN_DIR = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC


def _lstat(pkg: int, rel: str) -> os.stat_result | None:
    """`lstat` of `rel` in the package, or None if it (or a directory on the way) isn't there."""
    try:
        with beneath.parent(Root(pkg), rel.split("/"), symlinks=False) as (dir_fd, name):
            return os.stat(name, dir_fd=dir_fd, follow_symlinks=False)
    except OSError:
        return None


def _is(pkg: int, rel: str, kind) -> bool:
    st = _lstat(pkg, rel)
    return st is not None and kind(st.st_mode)


def read_manifest(pkg: int) -> tuple[Any, list[Diagnostic]]:
    """(parsed app.json or None, diagnostics about reading/parsing it)."""
    if _is(pkg, MANIFEST_FILE, stat.S_ISLNK):
        return None, [Diagnostic(MANIFEST_FILE, "", "app.json must be a file, not a symlink")]
    if not _is(pkg, MANIFEST_FILE, stat.S_ISREG):
        return None, [Diagnostic(MANIFEST_FILE, "", "app.json is missing")]
    try:
        with fsops.open_regular_at(pkg, MANIFEST_FILE) as f:
            raw = f.read(MAX_MANIFEST_BYTES + 1)
    except OSError as exc:
        return None, [Diagnostic(MANIFEST_FILE, "", f"app.json can't be read: {exc.strerror}")]
    if len(raw) > MAX_MANIFEST_BYTES:
        return None, [Diagnostic(MANIFEST_FILE, "", "app.json is larger than 256 KiB")]
    try:
        return json.loads(raw.decode("utf-8")), []
    except UnicodeDecodeError:
        return None, [Diagnostic(MANIFEST_FILE, "", "app.json is not valid UTF-8")]
    except json.JSONDecodeError as exc:
        return None, [
            Diagnostic(
                MANIFEST_FILE,
                "",
                f"app.json is not valid JSON: {exc.msg} (line {exc.lineno}, column {exc.colno})",
            )
        ]


def _required_file(pkg: int, rel: str, what: str) -> list[Diagnostic]:
    if _is(pkg, rel, stat.S_ISLNK):
        return [Diagnostic(rel, "", f"{rel} must be a file, not a symlink")]
    if _is(pkg, rel, stat.S_ISREG):
        return []
    return [Diagnostic(rel, "", f"{rel} is missing: {what}")]


def _check_segment(segment: str, *, last: bool) -> str | None:
    """Why `segment` isn't a valid route segment, or None."""
    if segment.startswith("(") and segment.endswith(")"):
        return "route groups like (name) aren't supported yet"
    if segment.startswith("+"):
        return f"special route files like {segment} aren't supported yet"
    if REST_SEGMENT_RE.fullmatch(segment):
        return None if last else "a catch-all [...name] must be the last segment (a file)"
    if PARAM_SEGMENT_RE.fullmatch(segment) or NAME_SEGMENT_RE.fullmatch(segment):
        return None
    return (
        f'"{segment}" is not a valid route segment: use a name (letters, digits, '
        "'-', '_'), index, [param] or [...rest]"
    )


class _Walk:
    """Directory walk that never follows symlinks and stops at MAX_PACKAGE_ENTRIES."""

    def __init__(self) -> None:
        self.seen = 0
        self.truncated = False

    def entries(self, dir_fd: int, rel: PurePosixPath):
        try:
            with os.scandir(dir_fd) as it:
                children = sorted(it, key=lambda e: e.name)
        except OSError:
            return
        for entry in children:
            if entry.name.startswith("."):
                continue
            self.seen += 1
            if self.seen > MAX_PACKAGE_ENTRIES:
                self.truncated = True
                return
            yield entry, rel / entry.name


def _open_dir(dir_fd: int, name: str) -> int | None:
    try:
        return os.open(name, _OPEN_DIR, dir_fd=dir_fd)
    except OSError:
        return None


def _route_diagnostics(pkg: int, walk: _Walk) -> list[Diagnostic]:
    out: list[Diagnostic] = []
    routes: dict[tuple[str, ...], str] = {}
    dynamic: dict[PurePosixPath, str] = {}

    def visit(dir_fd: int, dir_rel: PurePosixPath) -> None:
        for entry, rel in walk.entries(dir_fd, dir_rel):
            name = entry.name
            if entry.is_symlink():
                out.append(Diagnostic(str(rel), "", f"{rel} is a symlink; apps can't use symlinks"))
                continue
            top = rel.parent == PurePosixPath("app")
            if entry.is_dir(follow_symlinks=False):
                problem = _check_segment(name, last=False)
                if problem:
                    out.append(Diagnostic(str(rel), "", f"{rel}/: {problem}"))
                elif (fd := _open_dir(dir_fd, name)) is not None:
                    try:
                        visit(fd, rel)
                    finally:
                        os.close(fd)
                continue
            if not entry.is_file(follow_symlinks=False):
                out.append(Diagnostic(str(rel), "", f"{rel} is not a regular file"))
                continue
            stem, suffix = os.path.splitext(name)
            if suffix not in ROUTE_SUFFIXES or stem.endswith(".d"):
                out.append(
                    Diagnostic(
                        str(rel),
                        "",
                        f"{rel}: only .ts/.tsx route files may live under app/ "
                        "(put components and helpers in another folder, e.g. components/)",
                    )
                )
                continue
            if stem == "_layout":
                if not top:
                    out.append(
                        Diagnostic(
                            str(rel),
                            "",
                            f"{rel}: nested layouts aren't supported yet; only app/_layout.tsx",
                        )
                    )
                continue
            problem = _check_segment(stem, last=True)
            if problem:
                out.append(Diagnostic(str(rel), "", f"{rel}: {problem}"))
                continue
            segments = rel.parent.parts[1:] + (() if stem == "index" else (stem,))
            if segments in routes:
                out.append(
                    Diagnostic(
                        str(rel),
                        "",
                        f"{rel} and {routes[segments]} are the same route "
                        f"(/{'/'.join(segments)}); keep one",
                    )
                )
                continue
            routes[segments] = str(rel)
            if stem.startswith("["):
                other = dynamic.get(rel.parent)
                if other and PurePosixPath(other).stem != stem:
                    out.append(
                        Diagnostic(
                            str(rel),
                            "",
                            f"{rel} and {other} are both dynamic routes in the "
                            "same folder, so the router can't tell them apart; keep one",
                        )
                    )
                dynamic.setdefault(rel.parent, str(rel))

    if (fd := _open_dir(pkg, "app")) is not None:
        try:
            visit(fd, PurePosixPath("app"))
        finally:
            os.close(fd)
    return out


def _action_diagnostics(pkg: int, walk: _Walk, *, shipped: bool = False) -> list[Diagnostic]:
    out: list[Diagnostic] = []
    fd = _open_dir(pkg, "actions")
    if fd is None:
        return out
    allowed = " .sql or .json files" if shipped else " .sql files"
    name_ok = (
        (lambda n: ACTION_RE.fullmatch(n) or ACTION_JSON_RE.fullmatch(n))
        if shipped
        else ACTION_RE.fullmatch
    )
    example = "addItem.sql or moveToSpace.json" if shipped else "addItem.sql"
    try:
        for entry, rel in walk.entries(fd, PurePosixPath("actions")):
            if entry.is_symlink() or not entry.is_file(follow_symlinks=False):
                out.append(
                    Diagnostic(
                        str(rel),
                        "",
                        f"{rel}: actions/ may only contain{allowed}, one per action",
                    )
                )
            elif not name_ok(entry.name):
                out.append(
                    Diagnostic(
                        str(rel),
                        "",
                        f"{rel}: action file names must look like {example} "
                        "(a letter first, then letters, digits or '_')",
                    )
                )
    finally:
        os.close(fd)
    return out


def validate_package(
    pkg: int | None, slug: str, *, shipped: bool = False
) -> tuple[Any, list[Diagnostic]]:
    """(parsed manifest or None, every diagnostic) for the package folder open as `pkg`.

    `pkg` is None when there is no such folder. `shipped=True` is the
    image-shipped system-app layout (no `app/` UI; `.json` actions allowed).
    """
    if pkg is None:
        return None, [
            Diagnostic("", "", f"the app folder doesn't exist (expected .../Apps/{slug}/)")
        ]
    manifest, out = read_manifest(pkg)
    if manifest is not None:
        out += validate_manifest(manifest, shipped=shipped)
        declared = manifest.get("slug") if isinstance(manifest, dict) else None
        if isinstance(declared, str) and declared != slug:
            message = f'slug "{declared}" must equal the app\'s folder name "{slug}"'
            out.append(Diagnostic(MANIFEST_FILE, "/slug", message))
        if not shipped and slug in SYSTEM_APP_SLUGS:
            out.append(
                Diagnostic(
                    MANIFEST_FILE,
                    "/slug",
                    f'slug "{slug}" is reserved for an image-shipped system app',
                )
            )
    out += _required_file(pkg, "AGENT.md", "describe what the app does and its data for the agent")
    out += _required_file(pkg, "schema.sql", "the app's CREATE TABLE statements (may be empty)")
    walk = _Walk()
    if _is(pkg, "app", stat.S_ISDIR):
        out += _required_file(pkg, "app/_layout.tsx", "the root layout (e.g. a <Stack />)")
        out += _required_file(pkg, "app/index.tsx", "the home screen")
        out += _route_diagnostics(pkg, walk)
    elif _is(pkg, "app", stat.S_ISLNK):
        out.append(Diagnostic("app", "", "app/ must be a folder, not a symlink"))
    elif not shipped:
        out.append(
            Diagnostic("app", "", "app/ is missing: the folder of screens (_layout.tsx, index.tsx)")
        )
    if _is(pkg, "actions", stat.S_ISDIR):
        out += _action_diagnostics(pkg, walk, shipped=shipped)
    elif _lstat(pkg, "actions") is not None:
        kind = ".sql or .json files" if shipped else ".sql files"
        out.append(Diagnostic("actions", "", f"actions must be a folder of {kind}"))
    if walk.truncated:
        out.append(
            Diagnostic(
                "",
                "",
                f"the package has more than {MAX_PACKAGE_ENTRIES} files; "
                "only the first ones were checked",
            )
        )
    return manifest, out[:MAX_DIAGNOSTICS]

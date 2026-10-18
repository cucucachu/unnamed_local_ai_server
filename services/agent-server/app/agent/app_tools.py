"""The agent's app tools (M13-02, docs/PLATFORM.md §7 "Agent tools for apps").

`list_apps`, `create_app`, `build_app`, `app_query`, `app_update`,
`app_action`, `app_export`, `app_import` and `approve_migration`, each a thin client of the platform's
apps API acting as the user through the run's delegation
(`configurable["delegation"]`, as `PlatformFilesBackend` does). No
delegation, no request. Nothing here raises: a failure is a string the
model reads.

Reads and changes are separate tools (`app_query`, `app_update`) because
each tool has its own permission (#342, `app.agent.approvals`).
`app_update`, `app_action`, `app_import` and `approve_migration` ask from inside the
tool (`langgraph.types.interrupt`) rather than through
`HumanInTheLoopMiddleware`, because the card shows facts only the platform
knows (the instance's space, the action's SQL, the migration's steps).
Whether they ask is the user's tool policy, like every other tool.

The interrupt carries the same `HITLRequest` shape the middleware uses, plus
the call's `tool_call_id`, so `app/api/chat_ws.py` shows it as an ordinary
approval card. LangGraph re-runs an interrupted tool from the top on resume,
so everything before `interrupt()` is a read.

A destructive migration is applied only with a HITL marker
(`X-HomeAI-HITL-Approval`, `app.core.hitl` in the platform): minted here with
the service token once the user approved (on the card, or by allowing
`approve_migration`), bound to this thread, instance and migration,
single-use, never shown to the model.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Annotated, Any, Self
from uuid import UUID

import httpx
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool, InjectedToolCallId, tool
from langgraph.types import interrupt

from app.agent import approvals
from app.core.config import Settings

logger = logging.getLogger(__name__)

APP_TOOL_NAMES: tuple[str, ...] = (
    "list_apps",
    "create_app",
    "build_app",
    "app_query",
    "app_update",
    "app_action",
    "app_export",
    "app_import",
    "approve_migration",
)

API = "/api/platform"
HITL_HEADER = "X-HomeAI-HITL-Approval"
_HTTP_TIMEOUT_S = 60.0
# The platform's builder budget is 600 s per step (compile, smoke).
_BUILD_TIMEOUT_S = 1260.0
SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,39}$")
_MAX_ROWS_SHOWN = 200
_MAX_RESULT_CHARS = 20_000
_MAX_DIAGNOSTICS_SHOWN = 30
_TOKEN = re.compile(r"--[^\n]*|/\*.*?\*/|\s+|[A-Za-z_]+|.", re.DOTALL)
_DDL = frozenset(
    {"alter", "analyze", "attach", "begin", "commit", "create", "detach", "drop", "end",
     "reindex", "release", "rollback", "savepoint", "vacuum"}
)  # fmt: skip

SqlParams = list[str | int | float | bool | None] | dict[str, str | int | float | bool | None]


# --- platform calls ---------------------------------------------------------------------


@dataclass(frozen=True)
class _Reply:
    """A platform response; `status` 0 means none (unreachable)."""

    status: int
    body: Any = None
    content: bytes = b""

    @property
    def ok(self) -> bool:
        return 200 <= self.status < 300

    def field(self, name: str) -> Any:
        return self.body.get(name) if isinstance(self.body, dict) else None


def _delegation_token(config: RunnableConfig) -> str | None:
    token = getattr((config.get("configurable") or {}).get("delegation"), "token", None)
    return token if isinstance(token, str) and token else None


class _Platform:
    def __init__(self, settings: Settings, token: str) -> None:
        self.settings = settings
        self._client = httpx.AsyncClient(
            base_url=settings.platform_url,
            headers={"Authorization": f"Bearer {token}"},
            timeout=_HTTP_TIMEOUT_S,
        )
        self.token = token

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *_exc: object) -> None:
        await self._client.aclose()

    async def call(self, method: str, path: str, **kwargs: Any) -> _Reply:
        try:
            response = await self._client.request(method, path, **kwargs)
        except httpx.HTTPError as exc:
            logger.warning("app tools: %s %s failed: %r", method, path, exc)
            return _Reply(0)
        body = None
        if response.headers.get("content-type", "").startswith("application/json"):
            try:
                body = response.json()
            except ValueError:
                body = None
        return _Reply(response.status_code, body, response.content)

    async def mint_hitl_marker(self, instance_id: str, migration_id: str) -> _Reply:
        """`POST /internal/hitl-approvals` with the service token (never the model's to see)."""
        if not self.settings.platform_agent_token:
            return _Reply(0)
        return await self.call(
            "POST",
            "/internal/hitl-approvals",
            json={
                "delegation": self.token,
                "instance_id": instance_id,
                "migration_id": migration_id,
            },
            headers={"Authorization": f"Bearer {self.settings.platform_agent_token}"},
        )


def _import_error(reply: _Reply) -> str:
    """An import's failure, with the CSV line and column when the platform names them."""
    if reply.field("detail") != "import_failed":
        return f"Error: {_why(reply)}"
    where = [f"line {reply.field('line')}"] if reply.field("line") else []
    if reply.field("column"):
        where.append(f"column {reply.field('column')}")
    prefix = f"{', '.join(where)}: " if where else ""
    return f"Error: nothing was imported. {prefix}{reply.field('message')}"


def _why(reply: _Reply) -> str:
    """Why a platform call failed, for the model."""
    if reply.status == 0:
        return "the platform is unreachable"
    if reply.status == 401:
        return "not authorized (the user's session has ended)"
    detail = reply.field("detail")
    message = reply.field("message")
    if reply.status == 403 and detail == "insufficient_role":
        return "permission denied: the user can only view this space"
    if reply.status == 404:
        return "not found (or not visible to this user)"
    if message:
        return f"{detail}: {message}" if detail else str(message)
    if isinstance(detail, str):
        return detail
    return f"platform error {reply.status}"


# --- what the user can see ------------------------------------------------------------


def _space_path(space: dict) -> str:
    return "/personal" if space["kind"] == "personal" else f"/spaces/{space['slug']}"


def _space_label(space: dict) -> str:
    if space["kind"] == "personal":
        return "/personal (the user's personal space)"
    return f'/spaces/{space["slug"]} (shared space "{space["name"]}")'


def _data_path(space: dict, slug: str) -> str:
    if space["kind"] == "personal":
        return f"/app-data/personal/{slug}/data.sqlite"
    return f"/app-data/spaces/{space['slug']}/{slug}/data.sqlite"


@dataclass
class _Catalog:
    spaces: list[dict]
    instances: list[dict]  # each with "space" added
    apps: list[dict]
    system_apps: list[dict]

    def app(self, app_id: str) -> dict | None:
        return next((a for a in self.apps if a["id"] == app_id), None)

    def space_of(self, space_id: str) -> dict | None:
        return next((s for s in self.spaces if s["id"] == space_id), None)

    def system_app(self, ref: str) -> dict | None:
        key = ref.strip().strip("/")
        return next((a for a in self.system_apps if a["slug"] == key), None)


async def _catalog(api: _Platform, *, with_apps: bool = True) -> _Catalog | str:
    reply = await api.call("GET", f"{API}/spaces")
    if not reply.ok:
        return f"Error: couldn't list the user's spaces: {_why(reply)}"
    spaces = reply.body["spaces"]
    instances = []
    for space in spaces:
        reply = await api.call("GET", f"{API}/spaces/{space['id']}/instances")
        if not reply.ok:
            return f"Error: couldn't list the apps in {_space_path(space)}: {_why(reply)}"
        instances += [{**i, "space": space} for i in reply.body["instances"]]
    apps: list[dict] = []
    if with_apps:
        reply = await api.call("GET", f"{API}/apps")
        if not reply.ok:
            return f"Error: couldn't list apps: {_why(reply)}"
        apps = reply.body["apps"]
    reply = await api.call("GET", f"{API}/system-apps")
    if not reply.ok:
        return f"Error: couldn't list system apps: {_why(reply)}"
    system_apps = reply.body["apps"] if isinstance(reply.body, dict) else []
    return _Catalog(spaces, instances, apps, system_apps)


def _find_space(cat: _Catalog, ref: str) -> dict | str:
    ref = ref.strip().strip("/")
    if ref in ("personal", ""):
        found = [s for s in cat.spaces if s["kind"] == "personal"]
    else:
        slug = ref.removeprefix("spaces/")
        found = [s for s in cat.spaces if s["kind"] == "shared" and s["slug"] == slug]
    if not found:
        shared = ", ".join(s["slug"] for s in cat.spaces if s["kind"] == "shared") or "none"
        return (
            f"Error: no space '{ref}'. Use 'personal' or a shared space's slug "
            f"(the user's shared spaces: {shared})."
        )
    return found[0]


def _split_ref(ref: str) -> tuple[str | None, str]:
    """`<space>/<slug>`, `/spaces/<s>/Apps/<slug>`, `/personal/Apps/<slug>` or `<slug>`."""
    parts = [p for p in ref.strip().split("/") if p]
    if "Apps" in parts:
        i = parts.index("Apps")
        return "/".join(parts[:i]) or None, "/".join(parts[i + 1 :])
    if len(parts) >= 2:
        return "/".join(parts[:-1]), parts[-1]
    return None, parts[0] if parts else ""


def _in_space(space: dict, ref: str) -> bool:
    ref = ref.strip("/")
    if space["kind"] == "personal":
        return ref == "personal"
    return ref in (space["slug"], f"spaces/{space['slug']}")


def _is_uuid(ref: str) -> bool:
    try:
        UUID(ref.strip())
    except ValueError:
        return False
    return True


def _find_instance(cat: _Catalog, ref: str) -> dict | str:
    if _is_uuid(ref):
        found = [i for i in cat.instances if i["id"] == ref.strip().lower()]
    else:
        space_ref, slug = _split_ref(ref)
        found = [
            i
            for i in cat.instances
            if i["app"]["slug"] == slug and (space_ref is None or _in_space(i["space"], space_ref))
        ]
    if len(found) == 1:
        return found[0]
    if not found:
        return f"Error: no app instance '{ref}' visible to the user. Call list_apps to see them."
    options = ", ".join(f"{_space_path(i['space']).lstrip('/')}/{i['app']['slug']}" for i in found)
    return f"Error: '{ref}' is ambiguous; name the space too, one of: {options}."


def _find_app(cat: _Catalog, ref: str) -> dict | str:
    if _is_uuid(ref):
        found = [a for a in cat.apps if a["id"] == ref.strip().lower()]
    else:
        space_ref, slug = _split_ref(ref)
        found = []
        for app in cat.apps:
            space = cat.space_of(app["source_space_id"])
            if app["slug"] != slug or app["source_path"] is None:
                continue
            if space_ref is None or (space is not None and _in_space(space, space_ref)):
                found.append(app)
    if len(found) == 1:
        return found[0]
    if not found:
        return (
            f"Error: no app '{ref}' whose source the user can edit. Call list_apps to see them. "
            "A new app starts with create_app; files written into an Apps folder by hand "
            "aren't an app."
        )
    options = ", ".join(a["source_path"] for a in found)
    return f"Error: '{ref}' is ambiguous; use its source path, one of: {options}."


def _app_summary(app: dict | None, inst: dict) -> str:
    name = inst["app"]["name"]
    version = inst["app"].get("version")
    line = f"{name} (slug {inst['app']['slug']}" + (f", version {version})" if version else ")")
    description = None
    if app is not None and app.get("working_version"):
        description = (app["working_version"]["manifest"].get("homeai") or {}).get("description")
    return line + (f" — {description}" if description else "")


# --- approvals ------------------------------------------------------------------------


def _ask_user(
    name: str, args: dict, description: str, tool_call_id: str, extra: dict | None = None
) -> tuple[bool, str]:
    """Pause the run for the user's decision; `(approved, reason)`.

    `extra` goes on the card's action as is (e.g. an integration call's
    `integration`). The resume value only ever comes from `chat_ws.py`,
    built from the user's `approval_response` (or `cancel`) on their own
    socket.
    """
    response = interrupt(
        {
            "action_requests": [
                {
                    "name": name,
                    "args": args,
                    "description": description,
                    "tool_call_id": tool_call_id,
                    **(extra or {}),
                }
            ],
            "review_configs": [{"action_name": name, "allowed_decisions": ["approve", "reject"]}],
        }
    )
    decisions = response.get("decisions") if isinstance(response, dict) else None
    decision = decisions[0] if isinstance(decisions, list) and len(decisions) == 1 else None
    if isinstance(decision, dict) and decision.get("type") == "approve":
        return True, ""
    reason = decision.get("message") if isinstance(decision, dict) else None
    return False, reason if isinstance(reason, str) and reason else "The user rejected it."


def _system_app_lines(app: dict) -> list[str]:
    actions = app.get("actions") or []
    if actions:
        listed = ", ".join(
            f"{a['name']}({', '.join(a.get('params') or {})})" if isinstance(a, dict) else str(a)
            for a in actions
        )
    else:
        listed = "none"
    lines = [
        (
            f"- {app['name']} (slug {app['slug']}, version {app.get('version')}, "
            "image-shipped system app)"
        ),
        "  native host screen; source is read-only (not in the files tree)",
        f"  instance id: {app['slug']}",
        f"  actions: {listed}",
    ]
    if app.get("privileged"):
        lines.insert(2, "  privileged: " + ", ".join(app["privileged"]))
    if app.get("description"):
        lines.append(f"  {app['description']}")
    if app.get("agent_md"):
        lines.append("  AGENT.md:")
        lines.extend(f"    {line}" if line else "    " for line in app["agent_md"].splitlines())
    return lines


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def _cap(text: str) -> str:
    if len(text) <= _MAX_RESULT_CHARS:
        return text
    return text[:_MAX_RESULT_CHARS] + "\n[output truncated]"


def _rows(rows: list[dict]) -> str:
    if not rows:
        return "0 rows."
    shown = rows[:_MAX_ROWS_SHOWN]
    lines = [f"{len(rows)} row(s):"] + [_json(r) for r in shown]
    if len(rows) > len(shown):
        lines.append(f"[{len(rows) - len(shown)} more rows not shown; add a LIMIT or filter]")
    return _cap("\n".join(lines))


def _first_keyword(sql: str) -> str:
    for token in _TOKEN.findall(sql):
        if token.startswith(("--", "/*")) or token.isspace():
            continue
        return token.lower()
    return ""


def _steps(steps: list[dict]) -> str:
    lines = []
    for step in steps:
        lines.append(f"- [{step['kind']}] {step['op']} on {step['table']}: {step['reason']}")
        lines += [f"    {s}" for s in step.get("sql") or []]
    return "\n".join(lines)


def _migration_line(result: dict, inst: dict | None) -> str:
    where = (
        f"instance {result['instance_id']} in {_space_label(inst['space'])}"
        if inst is not None
        else f"instance {result['instance_id']}"
    )
    migration = result.get("migration")
    if result.get("error") and not migration:
        return f"- {where}: couldn't migrate ({result['error']})"
    status = migration["status"]
    if status == "up_to_date":
        return f"- {where}: database schema already up to date"
    if status == "applied":
        s = migration["summary"]
        return f"- {where}: schema migrated ({s.get('additive', 0)} additive, {s.get('safe', 0)} safe step(s))"
    if status == "pending":
        return (
            f"- {where}: a DESTRUCTIVE migration is pending (id {migration['id']}); it was NOT "
            f"applied:\n{_steps(migration['steps'])}\n  To apply it, call approve_migration("
            f'instance="{result["instance_id"]}", migration_id="{migration["id"]}"): the user '
            "has to approve it. Or change schema.sql so nothing is dropped and build again."
        )
    return f"- {where}: migration {status}: {migration.get('error') or result.get('error')}"


def _diagnostic(d: dict) -> str:
    where = d.get("file") or "?"
    if d.get("line"):
        where += f":{d['line']}" + (f":{d['column']}" if d.get("column") else "")
    line = f"- [{d.get('step', '?')}] {where}: {d.get('message', '')}"
    if d.get("source"):
        line += "\n" + "\n".join(f"    {s}" for s in str(d["source"]).splitlines()[:6])
    return line


# --- templates ------------------------------------------------------------------------


async def _templates(api: _Platform) -> list[dict] | str:
    """The platform's installable templates (`GET /app-templates`, M22-05)."""
    reply = await api.call("GET", f"{API}/app-templates")
    if not reply.ok:
        return f"Error: couldn't list the app templates: {_why(reply)}"
    return reply.body["templates"]


def _template_lines(templates: list[dict]) -> list[str]:
    return [f"  {t['slug']}: {t['description'] or t['name']}" for t in templates]


async def _tables(api: _Platform) -> dict[str, list[dict]]:
    """Each instance's tables, row counts and `schema.sql` descriptions (`GET /data-map`,
    M22-06); {} if the platform can't say: list_apps works without them."""
    reply = await api.call("GET", f"{API}/data-map")
    if not reply.ok:
        logger.warning("list_apps: data map unavailable: %s", _why(reply))
        return {}
    return {i["id"]: i.get("tables") or [] for i in reply.body.get("instances") or []}


def _table_lines(tables: list[dict]) -> list[str]:
    lines = []
    for t in tables:
        rows = "" if t.get("rows") is None else f" ({t['rows']:,} rows)"
        lines.append(
            f"  table {t['name']}{rows}" + (f": {t['description']}" if t.get("description") else "")
        )
        columns = t.get("columns") or {}
        if columns:
            lines.append("    " + "; ".join(f"{c}: {d}" for c, d in columns.items()))
    return lines


# --- the tools ------------------------------------------------------------------------


_NO_DELEGATION = "Error: apps are unavailable for this run (no delegation)."


def make_app_tools(settings: Settings) -> list[BaseTool]:
    """The six app tools, bound to one `Settings` (see `make_execute_code_tool`)."""

    @tool
    async def list_apps(config: RunnableConfig) -> str:
        """List the apps installed in the user's spaces, plus image-shipped system apps.

        System apps (Home, Chat, Files, Routines, Settings) ship in the platform image: native host
        screens, read-only source. Files has privileged actions (moveToSpace, copyToSpace).
        For each installed instance: its id, app name/slug/version, the space it's in and
        the user's role there, the app's source folder (edit it with the file tools) and
        where execute_code can read a read-only copy of its data, and its tables with
        row counts and what each table and column holds. Call this before using any
        other app tool.
        """
        token = _delegation_token(config)
        if token is None:
            return _NO_DELEGATION
        async with _Platform(settings, token) as api:
            cat = await _catalog(api)
            templates = await _templates(api)
            tables = await _tables(api) if not isinstance(cat, str) and cat.instances else {}
        if isinstance(cat, str):
            return cat
        if isinstance(templates, str):
            templates = []
        lines = []
        for app in cat.system_apps:
            lines += _system_app_lines(app)
        for inst in cat.instances:
            app = cat.app(inst["app_id"])
            space = inst["space"]
            lines.append(f"- {_app_summary(app, inst)}")
            lines.append(f"  instance id: {inst['id']}")
            lines.append(f"  in {_space_label(space)}; the user's role: {space['role']}")
            if app is not None and app.get("source_path"):
                built = (app.get("working_version") or {}).get("commit")
                lines.append(
                    f"  source: {app['source_path']} (app id {app['id']}; "
                    + (f"last built commit {built[:7]})" if built else "not built yet)")
                )
            lines.append(f"  execute_code read-only data: {_data_path(space, inst['app']['slug'])}")
            lines += _table_lines(tables.get(inst["id"], []))
        installed = {i["app_id"] for i in cat.instances}
        for app in cat.apps:
            if app["id"] not in installed and app.get("source_path"):
                lines.append(
                    f"- {app['name']} (slug {app['slug']}, not installed): source "
                    f"{app['source_path']} (app id {app['id']})"
                )
        starters = "\n".join(["Templates for create_app:", *_template_lines(templates)])
        if not lines:
            return f"The user has no apps yet. Create one with create_app.\n{starters}"
        return (
            "Apps (system apps are native host screens; an instance is an installed "
            "copy with its own database):\n"
            + "\n".join(lines)
            + "\nBefore changing a user app, read its app.json, AGENT.md and schema.sql. "
            "System-app source is image-shipped and read-only.\n" + starters
        )

    @tool
    async def create_app(
        space: str, slug: str, name: str, config: RunnableConfig, template: str = "grocery-list"
    ) -> str:
        """Create a new app from a template and install it in a space.

        space: "personal" or a shared space's slug. slug: the app's folder name, lowercase
        letters, digits and dashes (e.g. "chore-chart"). name: its display name.
        template: the starter to copy, by slug; list_apps lists them with what each is
        for (grocery-list is the default: a shopping list with quantities). Pick the
        closest. The source is copied to /personal/Apps/<slug>/ (or
        /spaces/<space>/Apps/<slug>/); read its AGENT.md, change the files with the file
        tools, then call build_app.
        """
        token = _delegation_token(config)
        if token is None:
            return _NO_DELEGATION
        if not SLUG_RE.fullmatch(slug):
            return "Error: slug must be 1-40 lowercase letters, digits or dashes, not starting with a dash."
        name = name.strip()
        if not name or len(name) > 64:
            return "Error: name must be 1-64 characters."
        async with _Platform(settings, token) as api:
            cat = await _catalog(api, with_apps=False)
            if isinstance(cat, str):
                return cat
            target = _find_space(cat, space)
            if isinstance(target, str):
                return target
            if target["role"] == "viewer":
                return f"Error: the user can only view {_space_label(target)}; they can't add apps there."
            base = f"{_space_path(target)}/Apps/{slug}"
            reply = (
                await api.call(
                    "POST",
                    f"{API}/spaces/{target['id']}/app-templates/{template}/install",
                    json={"slug": slug, "name": name, "build": False},
                )
                if SLUG_RE.fullmatch(template)
                else _Reply(404, {"detail": "unknown_template"})
            )
            if reply.status == 404 and reply.field("detail") == "unknown_template":
                templates = await _templates(api)
                if isinstance(templates, str):
                    return templates
                listed = "\n".join(_template_lines(templates))
                return f"Error: no template '{template}'. The templates:\n{listed}"
        if reply.status == 409 and reply.field("detail") == "app_exists":
            return f"Error: {base} already exists; pick another slug."
        if not reply.ok:
            diagnostics = reply.field("diagnostics") or []
            details = "\n".join(_diagnostic(d) for d in diagnostics)
            return f"Error: couldn't create {base}: {_why(reply)}" + (
                f"\n{details}" if details else ""
            )
        app, inst = reply.body["app"], reply.body["instance"]
        return (
            f"Created '{name}' from the {template} template in {_space_label(target)}.\n"
            f"source: {base}/\n"
            f"app id: {app['id']}; instance id: {inst['id']}\n"
            f"Next: read {base}/AGENT.md, edit the files for what the user wants, then call "
            f"build_app(app=\"{base}\"). It isn't built yet, so it doesn't run until then."
        )

    @tool
    async def build_app(app: str, config: RunnableConfig) -> str:
        """Verify and build an app from its source, then migrate its database.

        app: the source folder (e.g. /personal/Apps/chore-chart), its slug, or its app id.
        Returns the problems to fix (file, line, message) or success. On success each
        installed instance's database is migrated to schema.sql; a destructive change (a
        dropped table or column, a tightened type) is left pending until the user approves
        it through approve_migration.
        """
        token = _delegation_token(config)
        if token is None:
            return _NO_DELEGATION
        async with _Platform(settings, token) as api:
            cat = await _catalog(api)
            if isinstance(cat, str):
                return cat
            found = _find_app(cat, app)
            if isinstance(found, str):
                return found
            reply = await api.call(
                "POST", f"{API}/apps/{found['id']}/build", timeout=_BUILD_TIMEOUT_S
            )
        if not reply.ok:
            return f"Error: couldn't build {found['source_path']}: {_why(reply)}"
        body = reply.body
        diagnostics = body.get("diagnostics") or []
        if not body.get("ok"):
            shown = diagnostics[:_MAX_DIAGNOSTICS_SHOWN]
            more = len(diagnostics) - len(shown)
            return _cap(
                f"Build FAILED for {found['source_path']}: {len(diagnostics)} problem(s). "
                "Fix them with the file tools, then call build_app again.\n"
                + "\n".join(_diagnostic(d) for d in shown)
                + (f"\n[{more} more not shown]" if more > 0 else "")
            )
        build = body.get("build") or {}
        version = ((body.get("app") or {}).get("working_version") or {}).get("version")
        commit = build.get("commit")
        head = (
            f"Build succeeded: {found['name']}"
            + (f" {version}" if version else "")
            + f" (build {build.get('id')}"
            + (f", commit {commit[:7]}" if commit else "")
            + f", {build.get('duration_ms')} ms). The app now runs the new version."
        )
        by_id = {i["id"]: i for i in cat.instances}
        lines = [
            _migration_line(m, by_id.get(m["instance_id"])) for m in body.get("migrations") or []
        ]
        return head + ("\n" + "\n".join(lines) if lines else "\nNo installed instance to migrate.")

    def _sql_refusal(sql: str) -> str | None:
        keyword = _first_keyword(sql)
        if keyword in _DDL:
            return (
                f"Error: {keyword.upper()} isn't allowed here. Change the schema by editing the "
                "app's schema.sql and calling build_app."
            )
        return None

    @tool
    async def app_query(
        instance: str,
        sql: str,
        config: RunnableConfig,
        params: SqlParams | None = None,
    ) -> str:
        """Read an installed app's data with one SQLite SELECT.

        instance: the instance id from list_apps (or "<space>/<app-slug>"). sql: a single
        SELECT on the app's tables; params: values for ? (a list) or :name (an object).
        Returns the rows. To change data use app_update.
        """
        token = _delegation_token(config)
        if token is None:
            return _NO_DELEGATION
        if refusal := _sql_refusal(sql):
            return refusal
        async with _Platform(settings, token) as api:
            cat = await _catalog(api, with_apps=False)
            if isinstance(cat, str):
                return cat
            inst = _find_instance(cat, instance)
            if isinstance(inst, str):
                return inst
            rpc = f"{API}/apps/instances/{inst['id']}/rpc"
            reply = await api.call("POST", rpc, json={"op": "getAll", "sql": sql, "params": params})
        if reply.ok:
            return _rows(reply.body["rows"])
        if reply.field("detail") == "sql_not_allowed":
            return "Error: this statement changes data; app_query only reads. Use app_update."
        return f"Error: {_why(reply)}"

    @tool
    async def app_update(
        instance: str,
        sql: str,
        config: RunnableConfig,
        tool_call_id: Annotated[str, InjectedToolCallId],
        params: SqlParams | None = None,
    ) -> str:
        """Change an installed app's data with one SQLite INSERT, UPDATE or DELETE.

        instance: the instance id from list_apps (or "<space>/<app-slug>"). sql: a single
        INSERT, UPDATE or DELETE on the app's tables; params: values for ? (a list) or
        :name (an object). Returns how many rows changed. Schema changes go in schema.sql
        + build_app, never here. To read data use app_query. May need the user's approval.
        """
        token = _delegation_token(config)
        if token is None:
            return _NO_DELEGATION
        if refusal := _sql_refusal(sql):
            return refusal
        if _first_keyword(sql) == "select":
            return "Error: this statement only reads; use app_query."
        async with _Platform(settings, token) as api:
            cat = await _catalog(api, with_apps=False)
            if isinstance(cat, str):
                return cat
            inst = _find_instance(cat, instance)
            if isinstance(inst, str):
                return inst
            rpc = f"{API}/apps/instances/{inst['id']}/rpc"
            if inst["space"]["role"] == "viewer":
                return f"Error: the user can only view {_space_label(inst['space'])}; this statement writes."
            if approvals.needs_approval(config, "app_update", tool_call_id):
                description = (
                    f"Change data in {inst['app']['name']} in {_space_label(inst['space'])}:\n{sql}"
                    + (f"\nparams: {_json(params)}" if params else "")
                )
                args = {"instance": inst["id"], "space": _space_path(inst["space"]), "sql": sql}
                if params:
                    args["params"] = params
                approved, reason = _ask_user("app_update", args, description, tool_call_id)
                if not approved:
                    return f"The user rejected this statement; it was not run. {reason}"
            reply = await api.call("POST", rpc, json={"op": "run", "sql": sql, "params": params})
        if not reply.ok:
            return f"Error: {_why(reply)}"
        return f"OK: {reply.body['changes']} row(s) changed; lastInsertRowId {reply.body['lastInsertRowId']}."

    @tool
    async def app_action(
        instance: str,
        name: str,
        config: RunnableConfig,
        tool_call_id: Annotated[str, InjectedToolCallId],
        params: dict[str, str | int | float | bool | None] | None = None,
    ) -> str:
        """Run one of an app's named actions.

        For a user-app instance: its actions/<name>.sql, instance id from list_apps (or
        "<space>/<app-slug>"). For an image-shipped system app: instance is the slug
        (e.g. "files") and name is a privileged action such as moveToSpace. Params are
        an object. May need the user's approval.
        """
        token = _delegation_token(config)
        if token is None:
            return _NO_DELEGATION
        async with _Platform(settings, token) as api:
            cat = await _catalog(api)
            if isinstance(cat, str):
                return cat
            sysapp = cat.system_app(instance)
            if sysapp is not None:
                action_params = dict(params or {})
                known = {a["name"] for a in sysapp.get("actions") or [] if isinstance(a, dict)}
                if name not in known:
                    return f"Error: {sysapp['name']} has no action '{name}'."
                if approvals.needs_approval(config, "app_action", tool_call_id):
                    description = f"Run action {name} of system app {sysapp['name']}" + (
                        f" with {_json(action_params)}" if action_params else ""
                    )
                    args = {"instance": sysapp["slug"], "name": name, "params": action_params}
                    approved, reason = _ask_user("app_action", args, description, tool_call_id)
                    if not approved:
                        return f"The user rejected this action; it was not run. {reason}"
                reply = await api.call(
                    "POST",
                    f"{API}/system-apps/{sysapp['slug']}/actions/{name}",
                    json={"params": action_params},
                )
                if not reply.ok:
                    return f"Error: {_why(reply)}"
                result = reply.field("result") if isinstance(reply.body, dict) else None
                if isinstance(result, dict) and "src" in result and "dst" in result:
                    verb = "moved" if name == "moveToSpace" else "copied"
                    return f"OK: {verb} {result['src']} to {result['dst']}."
                return f"OK: {_json(result if result is not None else reply.body)}"
            inst = _find_instance(cat, instance)
            if isinstance(inst, str):
                return inst
            if inst["space"]["role"] == "viewer":
                return f"Error: the user can only view {_space_label(inst['space'])}; actions change data."
            if approvals.needs_approval(config, "app_action", tool_call_id):
                app = cat.app(inst["app_id"]) or {}
                action_sql = "(the action's SQL isn't visible to this user)"
                if app.get("source_path"):
                    reply = await api.call(
                        "GET",
                        f"{API}/files/download",
                        params={"path": f"{app['source_path']}/actions/{name}.sql"},
                    )
                    if reply.status == 404:
                        return f"Error: {inst['app']['name']} has no action '{name}' (actions/{name}.sql)."
                    if reply.ok:
                        action_sql = reply.content.decode("utf-8", errors="replace")
                description = (
                    f"Run action {name} of {inst['app']['name']} in {_space_label(inst['space'])}"
                    + (f" with {_json(params)}" if params else "")
                    + f":\n{action_sql}"
                )
                args = {"instance": inst["id"], "space": _space_path(inst["space"]), "name": name}
                if params:
                    args["params"] = params
                approved, reason = _ask_user("app_action", args, description, tool_call_id)
                if not approved:
                    return f"The user rejected this action; it was not run. {reason}"
            reply = await api.call(
                "POST",
                f"{API}/apps/instances/{inst['id']}/rpc",
                json={"op": "action", "name": name, "params": params or {}},
            )
        if not reply.ok:
            return f"Error: {_why(reply)}"
        body = reply.body
        out = f"OK: {body['changes']} row(s) changed; lastInsertRowId {body['lastInsertRowId']}."
        return out + ("\n" + _rows(body["rows"]) if body.get("rows") else "")

    @tool
    async def app_export(instance: str, config: RunnableConfig, folder: str | None = None) -> str:
        """Save a copy of an installed app's data in Files: each table as <table>.csv plus
        schema.sql, in a new folder.

        instance: the instance id from list_apps (or "<space>/<app-slug>"). folder: a new
        folder path (/personal/... or /spaces/<slug>/...); by default Exports/<slug>-<time> in
        the app's space. Returns the folder and each file's row count.
        """
        token = _delegation_token(config)
        if token is None:
            return _NO_DELEGATION
        async with _Platform(settings, token) as api:
            cat = await _catalog(api, with_apps=False)
            if isinstance(cat, str):
                return cat
            inst = _find_instance(cat, instance)
            if isinstance(inst, str):
                return inst
            body = {"folder": folder} if folder else {}
            reply = await api.call("POST", f"{API}/apps/instances/{inst['id']}/export", json=body)
        if reply.status == 409:
            return f"Error: {folder or 'the export folder'} already exists; pick a new folder."
        if not reply.ok:
            return f"Error: {_why(reply)}"
        files = [f"{t['file']} ({t['rows']} rows)" for t in reply.body["tables"]]
        return f"OK: exported to {reply.body['folder']}: {', '.join([*files, 'schema.sql'])}."

    @tool
    async def app_import(
        instance: str,
        table: str,
        path: str,
        config: RunnableConfig,
        tool_call_id: Annotated[str, InjectedToolCallId],
        mapping: dict[str, str] | None = None,
        delimiter: str = ",",
    ) -> str:
        """Import a CSV file from Files into one table of an installed app, all rows or none.

        instance: the instance id from list_apps (or "<space>/<app-slug>"). path: the CSV
        file (/personal/... or /spaces/<slug>/...); its first line names the columns.
        mapping: {"CSV header": "table column"} for the headers to import (the rest are
        skipped); without it every header must be a column name. delimiter: "," (default),
        ";" or a tab. Empty fields take the column's default. Much faster than many
        app_update calls. May need the user's approval.
        """
        token = _delegation_token(config)
        if token is None:
            return _NO_DELEGATION
        if delimiter not in (",", ";", "\t"):
            return 'Error: delimiter must be ",", ";" or a tab.'
        async with _Platform(settings, token) as api:
            cat = await _catalog(api, with_apps=False)
            if isinstance(cat, str):
                return cat
            inst = _find_instance(cat, instance)
            if isinstance(inst, str):
                return inst
            if inst["space"]["role"] == "viewer":
                return f"Error: the user can only view {_space_label(inst['space'])}; importing writes."
            url = f"{API}/apps/instances/{inst['id']}/import"
            body = {"table": table, "path": path, "mapping": mapping, "delimiter": delimiter}
            check = await api.call("POST", url, json={**body, "dry_run": True})
            if not check.ok:
                return _import_error(check)
            plan = check.body
            if plan["unmatched"]:
                return (
                    f"Error: no column of {table} for {', '.join(plan['unmatched'])}. Give a "
                    f"mapping from the file's headers ({', '.join(plan['headers'])}) to the "
                    f"table's columns ({', '.join(plan['columns'])}); unmapped headers are skipped."
                )
            columns = ", ".join(f"{h} -> {c}" for h, c in plan["mapping"].items())
            if approvals.needs_approval(config, "app_import", tool_call_id):
                description = (
                    f"Import {plan['rows']} rows from {path} into {table} of "
                    f"{inst['app']['name']} in {_space_label(inst['space'])}: {columns}"
                )
                args = {"instance": inst["id"], "space": _space_path(inst["space"]), **body}
                approved, reason = _ask_user("app_import", args, description, tool_call_id)
                if not approved:
                    return f"The user rejected this import; nothing was imported. {reason}"
            reply = await api.call("POST", url, json=body)
        if not reply.ok:
            return _import_error(reply)
        skipped = reply.body["skipped"]
        return f"OK: imported {reply.body['rows']} rows into {table} ({columns})." + (
            f" Skipped: {', '.join(skipped)}." if skipped else ""
        )

    @tool
    async def approve_migration(
        instance: str,
        migration_id: str,
        config: RunnableConfig,
        tool_call_id: Annotated[str, InjectedToolCallId],
    ) -> str:
        """Apply a pending destructive migration that build_app reported.

        Unless the user allows this tool, they see the exact steps and decide first; the
        database is snapshotted before it's applied. Only call this when the user wants that change (e.g. they
        asked to remove a field); otherwise change schema.sql so nothing is dropped.
        """
        token = _delegation_token(config)
        if token is None:
            return _NO_DELEGATION
        async with _Platform(settings, token) as api:
            cat = await _catalog(api, with_apps=False)
            if isinstance(cat, str):
                return cat
            inst = _find_instance(cat, instance)
            if isinstance(inst, str):
                return inst
            base = f"{API}/apps/instances/{inst['id']}/migrations"
            reply = await api.call("GET", base)
            if not reply.ok:
                return f"Error: {_why(reply)}"
            migration = next(
                (m for m in reply.body["migrations"] if m["id"] == migration_id.strip().lower()),
                None,
            )
            if migration is None:
                return f"Error: no migration {migration_id} for this instance."
            if migration["status"] != "pending":
                return f"Migration {migration_id} is {migration['status']}, not pending; nothing to approve."
            description = (
                f"Apply a DESTRUCTIVE database change to {inst['app']['name']} in "
                f"{_space_label(inst['space'])}. Data in dropped tables or columns is lost "
                f"(a snapshot is taken first):\n{_steps(migration['steps'])}"
            )
            args = {
                "instance": inst["id"],
                "space": _space_path(inst["space"]),
                "migration_id": migration["id"],
                "steps": [s for step in migration["steps"] for s in step.get("sql") or []],
            }
            if approvals.needs_approval(config, "approve_migration", tool_call_id):
                approved, reason = _ask_user("approve_migration", args, description, tool_call_id)
                if not approved:
                    return (
                        "The user rejected the migration; it was not applied and stays pending "
                        f"(they can still approve or reject it in the Apps screen). {reason}"
                    )
            marker = await api.mint_hitl_marker(inst["id"], migration["id"])
            if not marker.ok:
                return f"Error: couldn't record the user's approval: {_why(marker)}"
            reply = await api.call(
                "POST",
                f"{base}/{migration['id']}/approve",
                headers={HITL_HEADER: marker.body["token"]},
            )
        if not reply.ok:
            return f"Error: the migration was approved but not applied: {_why(reply)}"
        done = reply.body
        return (
            f"Migration {done['id']} {done['status']}"
            + (f" (snapshot {done['snapshot']})" if done.get("snapshot") else "")
            + "."
        )

    return [
        list_apps,
        create_app,
        build_app,
        app_query,
        app_update,
        app_action,
        app_export,
        app_import,
        approve_migration,
    ]

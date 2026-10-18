"""Request/response bodies for `/api/auth/*` and `/api/platform/*`.

Documented field-by-field in docs/ARCHITECTURE.md §3 "Platform API"; keep
the two in sync. String caps here only bound payload size - the real rules
(username format, password length, ...) live in `app/core/passwords.py` so
they produce specific error codes.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    model_validator,
)

Short = Annotated[str, Field(max_length=256)]
Secret = Annotated[str, Field(max_length=4096)]


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    username: str
    display_name: str
    role: Literal["admin", "member"]
    totp_enabled: bool
    require_passkeys: bool = False
    disabled_at: datetime | None
    created_at: datetime


class SessionResponse(BaseModel):
    user: UserOut
    session_token: str | None = None


class WebAuthnStatus(BaseModel):
    """Enough for the UI to offer passkeys. No secrets."""

    rp_id: str | None = None
    origin_ok: bool = False


class StatusResponse(BaseModel):
    setup_required: bool
    authenticated: bool
    user: UserOut | None = None
    webauthn: WebAuthnStatus = WebAuthnStatus()
    # M15-05: DB flag (not a secret). `origin` is this request's classifier
    # so the login UI can hide the password when the flag is on *and* the
    # client is public. Expo Go (`native`) still uses password; the host
    # app (`host`) uses device pairing (M15-06) with password as LAN fallback.
    public_https: bool = False
    origin: Literal["lan", "vpn", "public"] = "lan"


class SetupRequest(BaseModel):
    setup_code: Short
    username: Short
    display_name: Short
    password: Secret
    device_label: Short | None = None


class LoginRequest(BaseModel):
    username: Short
    password: Secret
    totp_code: Short | None = None
    device_label: Short | None = None
    # Optional WireGuard peer this login came from (M15-01). Revoking that
    # device then drops this session without signing out other devices.
    device_id: UUID | None = None


class StepUpRequest(BaseModel):
    password: Secret


class StepUpResponse(BaseModel):
    stepped_up_until: datetime


class InviteAcceptRequest(BaseModel):
    token: Short
    username: Short
    display_name: Short
    password: Secret
    device_label: Short | None = None


class MePatchRequest(BaseModel):
    display_name: Short | None = None
    password: Secret | None = None
    current_password: Secret | None = None


class SessionOut(BaseModel):
    id: UUID
    device_label: str | None
    # Set for a routine grant (an unattended run's identity), not a device.
    routine_id: str | None = None
    created_at: datetime
    last_seen_at: datetime
    expires_at: datetime
    current: bool


class SessionList(BaseModel):
    sessions: list[SessionOut]


class WireGuardDeviceCreateRequest(BaseModel):
    name: Short


class WireGuardDeviceOut(BaseModel):
    id: UUID
    name: str
    address: str
    created_at: datetime


class WireGuardDeviceCreated(WireGuardDeviceOut):
    """Returned only from create: `config` is the wg-quick text (and QR payload).
    The peer private key is in that text and is never stored."""

    config: str


class WireGuardDeviceList(BaseModel):
    devices: list[WireGuardDeviceOut]


class PasswordRequest(BaseModel):
    password: Secret


class TotpEnrollResponse(BaseModel):
    secret: str
    otpauth_uri: str


class TotpConfirmRequest(BaseModel):
    code: Short


class UserList(BaseModel):
    users: list[UserOut]


class AdminUserPatch(BaseModel):
    role: Literal["admin", "member"] | None = None
    disabled: bool | None = None
    require_passkeys: bool | None = None


class PlatformSettingsOut(BaseModel):
    public_https: bool
    domain_configured: bool


class PlatformSettingsPatch(BaseModel):
    public_https: bool


class PasskeyLoginBeginRequest(BaseModel):
    username: Short


class PasskeyCredentialRequest(BaseModel):
    credential: dict[str, Any]


class PasskeyLoginFinishRequest(PasskeyCredentialRequest):
    username: Short
    totp_code: Short | None = None
    device_label: Short | None = None
    device_id: UUID | None = None


class PasskeyRegisterFinishRequest(PasskeyCredentialRequest):
    name: Short | None = None


class PasskeyOut(BaseModel):
    id: UUID
    name: str | None
    transports: list[str] | None
    created_at: datetime
    last_used_at: datetime | None


class PasskeyList(BaseModel):
    passkeys: list[PasskeyOut]


class DevicePairOut(BaseModel):
    id: UUID
    name: str
    created_at: datetime
    last_used_at: datetime | None


class DevicePairList(BaseModel):
    devices: list[DevicePairOut]


class DevicePairBeginResponse(BaseModel):
    """LAN QR payload plus `expires_at` (not encoded in the QR)."""

    v: int
    kind: Literal["homeai-host-pair"]
    token: str
    challenge: str
    user: str
    expires_at: datetime


class DeviceEnrollRequest(BaseModel):
    token: Short
    public_key: Secret
    name: Short
    signature: Secret


class DeviceLoginBeginRequest(BaseModel):
    device_id: UUID


class DeviceLoginBeginResponse(BaseModel):
    challenge: str
    expires_at: datetime


class DeviceLoginFinishRequest(BaseModel):
    device_id: UUID
    signature: Secret
    totp_code: Short | None = None
    device_label: Short | None = None


class InviteCreateRequest(BaseModel):
    label: Short | None = None


class InviteOut(BaseModel):
    id: UUID
    label: str | None
    status: Literal["pending", "used", "expired", "revoked"]
    created_by: UUID | None
    created_at: datetime
    expires_at: datetime
    used_at: datetime | None
    used_by: UUID | None
    revoked_at: datetime | None


class InviteCreated(InviteOut):
    token: str
    accept_url: str


class InviteList(BaseModel):
    invites: list[InviteOut]


class DirectoryUser(BaseModel):
    id: UUID
    username: str
    display_name: str


class Directory(BaseModel):
    users: list[DirectoryUser]


SpaceRole = Literal["owner", "editor", "viewer"]


class SpaceOut(BaseModel):
    id: UUID
    slug: str
    name: str
    kind: Literal["personal", "shared"]
    gid: int
    owner_user_id: UUID | None
    role: SpaceRole | None
    created_at: datetime
    archived_at: datetime | None


class SpaceList(BaseModel):
    spaces: list[SpaceOut]


class SpaceCreateRequest(BaseModel):
    slug: Short
    name: Short


class SpacePatchRequest(BaseModel):
    name: Short


class MemberOut(BaseModel):
    user_id: UUID
    username: str
    display_name: str
    role: SpaceRole
    added_at: datetime


class MemberList(BaseModel):
    members: list[MemberOut]


class AuditEventOut(BaseModel):
    id: int
    at: datetime
    kind: str
    actor_kind: Literal["user", "agent", "system"]
    actor_user_id: UUID | None
    actor_name: str | None
    session_id: UUID | None
    thread_id: str | None
    space_id: UUID | None
    target_type: str | None
    target_id: str | None
    summary: str
    detail: dict[str, Any]


class AuditEventList(BaseModel):
    events: list[AuditEventOut]
    # Pass as `before` for the next (older) page; null when there's no more.
    next_before: int | None


class MemberAddRequest(BaseModel):
    user_id: UUID
    role: SpaceRole


class MemberPatchRequest(BaseModel):
    role: SpaceRole


# --- files (virtual paths: `/personal/...`, `/spaces/<slug>/...`) -------------

VPath = Annotated[str, Field(max_length=4096)]


class FileEntryOut(BaseModel):
    name: str
    path: str
    type: Literal["file", "dir"]
    size: int
    mtime: datetime
    mime: str | None
    # Only on the synthetic entries (`/personal`, `/spaces`, `/spaces/<slug>`).
    label: str | None = None
    role: SpaceRole | None = None


class FileListOut(BaseModel):
    path: str
    entries: list[FileEntryOut]
    role: SpaceRole | None
    writable: bool
    # The listed space's display name ("Personal" or the space's name); None for `/`, `/spaces`.
    space_label: str | None = None


class FileStatOut(BaseModel):
    entry: FileEntryOut
    role: SpaceRole | None
    writable: bool


class UploadOut(BaseModel):
    uploaded: list[str]


class PathBody(BaseModel):
    path: VPath


class PathOut(BaseModel):
    path: str


class MoveCopyBody(BaseModel):
    src: VPath
    dst: VPath


class RenameBody(BaseModel):
    path: VPath
    name: Short


class MoveCopyOut(BaseModel):
    src: str
    dst: str


class ReadBody(BaseModel):
    path: VPath
    offset: int = 0
    limit: int = 2000


class ReadOut(BaseModel):
    path: str
    content: str
    encoding: Literal["utf-8", "base64"]
    total_lines: int | None = None
    start_line: int | None = None
    end_line: int | None = None
    next_offset: int | None = None
    no_lines_requested: bool = False


class WriteBody(BaseModel):
    path: VPath
    content: str


class EditBody(BaseModel):
    path: VPath
    old_string: str
    new_string: str
    replace_all: bool = False


class EditOut(BaseModel):
    path: str
    occurrences: int
    # Set when the edit matched only after ignoring whitespace.
    note: str | None = None


class GrepBody(BaseModel):
    pattern: str = Field(min_length=1, max_length=4096)
    path: VPath | None = None
    glob: Short | None = None
    max_count: int | None = Field(default=None, ge=0)


class SearchBody(BaseModel):
    query: str = Field(min_length=1, max_length=2000)
    path: VPath = "/"
    limit: int = Field(default=10, ge=1, le=50)


class SearchAppOut(BaseModel):
    slug: str
    name: str


class SearchSpaceOut(BaseModel):
    id: UUID
    slug: str
    name: str
    kind: str


class SearchAppRowOut(BaseModel):
    instance_id: UUID
    app: SearchAppOut
    space: SearchSpaceOut
    table: str
    row_id: int
    title: str


class SearchHitOut(BaseModel):
    # `file`: path and lines are set; `app_row` (M22-07): a row an app declared in `homeai.index`.
    kind: Literal["file", "app_row"] = "file"
    path: str | None = None
    line_start: int | None = None
    line_end: int | None = None
    app_row: SearchAppRowOut | None = None
    snippet: str
    score: float


class SearchOut(BaseModel):
    results: list[SearchHitOut]
    # False: the embeddings service wasn't reachable, so only full-text matching ran.
    semantic: bool


class GrepMatchOut(BaseModel):
    path: str
    line: int
    text: str


class GrepOut(BaseModel):
    matches: list[GrepMatchOut]
    truncated: bool
    error: str | None


class GlobBody(BaseModel):
    pattern: Short
    path: VPath | None = None


class GlobMatchOut(BaseModel):
    path: str
    is_dir: bool
    size: int | None = None
    modified_at: datetime | None = None


class GlobOut(BaseModel):
    matches: list[GlobMatchOut]
    truncated: bool
    truncation_reason: Literal["budget"] | None


# --- delegations (`/internal/delegations*`) -----------------------------------


class DelegationRequest(BaseModel):
    identity_token: Secret
    thread_id: Short


class DelegationRefreshRequest(BaseModel):
    token: Secret


class DelegationOut(BaseModel):
    token: str
    expires_at: datetime


class HitlApprovalRequest(BaseModel):
    delegation: Secret
    instance_id: UUID
    migration_id: UUID


class HitlApprovalOut(BaseModel):
    token: str
    expires_in_s: float


class SpaceAccessRequest(BaseModel):
    # Exactly one: a person's identity token, or a chat's delegation (M17-07).
    identity_token: Secret | None = None
    delegation_token: Secret | None = None
    space: Short


class SpaceAccessOut(BaseModel):
    space: str
    role: str


class RoutineGrantRequest(BaseModel):
    # Exactly one, as for `SpaceAccessRequest`.
    identity_token: Secret | None = None
    delegation_token: Secret | None = None
    routine_id: Short
    space: Short
    label: Short | None = None


class RoutineGrantOut(BaseModel):
    grant: str
    grant_id: UUID
    space: str


class RoutineGrantExchangeRequest(BaseModel):
    grant: Secret
    routine_id: Short
    thread_id: Short


class RoutineGrantRevokeRequest(BaseModel):
    grant: Secret


class ExecGrantsRequest(BaseModel):
    delegation: Secret


class ExecMountOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    host_path: str
    container_path: str
    read_only: bool


class ExecGrantsOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    uid: int
    gid: int
    gids: list[int]
    mounts: list[ExecMountOut]


# --- apps -------------------------------------------------------------------------


class DiagnosticOut(BaseModel):
    file: str
    path: str
    message: str


class AppVersionOut(BaseModel):
    id: UUID
    version: str
    kind: Literal["working", "published"]
    commit: str | None
    manifest: dict[str, Any]
    bundle_path: str | None
    created_at: datetime
    published_at: datetime | None


class AppOut(BaseModel):
    id: UUID
    slug: str
    name: str
    source_space_id: UUID
    # Both None when the caller sees the app only through an install.
    source_path: str | None
    working_version: AppVersionOut | None
    created_by: UUID | None
    created_at: datetime
    archived_at: datetime | None


class AppList(BaseModel):
    apps: list[AppOut]


class AppRegisterRequest(BaseModel):
    source_path: VPath


class AppValidationOut(BaseModel):
    app: AppOut
    valid: bool
    diagnostics: list[DiagnosticOut]


class BuildDiagnosticOut(BaseModel):
    """`DiagnosticOut` plus the build step and a 1-based position, when there is one."""

    step: Literal[
        "manifest", "files", "route", "import", "bundle", "type", "render", "sql", "build"
    ]
    file: str
    path: str
    line: int | None
    column: int | None
    message: str
    source: str | None = None


class BuildOut(BaseModel):
    id: str
    duration_ms: int
    bundle_path: str | None
    bundle_bytes: int | None
    # The history commit of what was built; None for a failed build (or no usable history).
    commit: str | None = None


class AppBuildOut(BaseModel):
    app: AppOut
    ok: bool
    # None when it stopped before the builder ran (the package itself failed).
    build: BuildOut | None
    diagnostics: list[BuildDiagnosticOut]
    # One per live instance tracking the working version, after a successful build.
    migrations: list[InstanceMigrationOut] = []


class AppCommitOut(BaseModel):
    id: str
    parent: str | None
    kind: Literal["build", "revert"]
    subject: str
    version: str | None
    # Username of who built or reverted; the thread when it was their agent.
    user: str | None
    thread_id: str | None
    # For a revert: the commit whose tree it restored.
    reverts: str | None
    created_at: datetime
    # The working version was built from this commit.
    current: bool


class AppHistoryOut(BaseModel):
    commits: list[AppCommitOut]
    # Pass as `offset` for the next (older) page; None on the last one.
    next_offset: int | None


class AppRevertRequest(BaseModel):
    commit: Annotated[str, Field(pattern=r"^[0-9a-f]{7,40}$")]


class AppRevertOut(AppBuildOut):
    # The history's head after the revert: a commit with the target's tree.
    commit: str


class InstanceAppOut(BaseModel):
    id: UUID
    slug: str
    name: str
    version: str | None
    icon: str | None


class UpdateAvailableOut(BaseModel):
    id: UUID
    version: str
    permissions: dict[str, Any]


class InstanceOut(BaseModel):
    id: UUID
    app_id: UUID
    space_id: UUID
    # "working", or the pinned published version's id.
    tracks: str
    installed_by: UUID | None
    granted_permissions: dict[str, Any]
    granted_reads: list[Any] = []
    created_at: datetime
    app: InstanceAppOut
    # Newer published version of the same app, when this instance pins one.
    update: UpdateAvailableOut | None = None


class InstanceList(BaseModel):
    instances: list[InstanceOut]


class InstallRequest(BaseModel):
    app_id: UUID
    tracks: Short = "working"
    granted_permissions: dict[str, Any] | None = None
    granted_reads: list[Any] | None = None


class PublishRequest(BaseModel):
    space_ids: Annotated[list[UUID], Field(min_length=1, max_length=100)]


class PublishOut(BaseModel):
    app: AppOut
    version: AppVersionOut
    space_ids: list[UUID]


class CatalogEntryOut(BaseModel):
    app: InstanceAppOut
    version: AppVersionOut
    installed: bool
    instance_id: UUID | None


class CatalogList(BaseModel):
    entries: list[CatalogEntryOut]


class UpdateInstanceRequest(BaseModel):
    version_id: UUID
    granted_permissions: dict[str, Any] | None = None
    granted_reads: list[Any] | None = None


class ForkRequest(BaseModel):
    space_id: UUID
    slug: Short | None = None


class ForkOut(BaseModel):
    app: AppOut
    instance: InstanceOut


class DataMapTable(BaseModel):
    name: str
    rows: int | None
    description: str | None
    columns: dict[str, str]


class DataMapApp(BaseModel):
    id: UUID
    slug: str
    name: str


class DataMapSpace(BaseModel):
    id: UUID
    slug: str
    name: str
    kind: str
    role: str


class DataMapInstance(BaseModel):
    id: UUID
    app: DataMapApp
    space: DataMapSpace
    description: str | None
    tables: list[DataMapTable]


class DataMapOut(BaseModel):
    """`GET /api/platform/data-map` (M22-06)."""

    instances: list[DataMapInstance]


class AppTemplateOut(BaseModel):
    slug: str
    name: str
    version: str
    icon: str | None
    description: str
    agent_md: str


class AppTemplateList(BaseModel):
    templates: list[AppTemplateOut]


class TemplateInstallRequest(BaseModel):
    """`slug` / `name` default to the template's; `build: false` leaves it unbuilt (the agent
    edits it first)."""

    slug: Short | None = None
    name: Annotated[str, Field(max_length=200)] | None = None
    build: bool = True


class TemplateInstallOut(BaseModel):
    app: AppOut
    instance: InstanceOut
    # None when `build` was false or the builder is unavailable.
    build: AppBuildOut | None


# --- app data (M12-03) ------------------------------------------------------------

SqlText = Annotated[str, Field(min_length=1, max_length=100 * 1024)]
SqlValue = str | bool | int | float | None
# expo-sqlite's bind params: positional for `?`, or named (`:x`, `$x`, `@x`; prefix optional).
SqlParams = (
    Annotated[list[SqlValue], Field(max_length=1000)]
    | Annotated[dict[Short, SqlValue], Field(max_length=1000)]
    | None
)


class InstanceBundleOut(BaseModel):
    app_id: UUID
    version: str
    sdk: str
    bundle_id: str
    code: str


class RpcGetAll(BaseModel):
    op: Literal["getAll"]
    sql: SqlText
    params: SqlParams = None


class RpcGetFirst(BaseModel):
    op: Literal["getFirst"]
    sql: SqlText
    params: SqlParams = None


class RpcRun(BaseModel):
    op: Literal["run"]
    sql: SqlText
    params: SqlParams = None


class RpcStatement(BaseModel):
    sql: SqlText
    params: SqlParams = None


class RpcTransaction(BaseModel):
    op: Literal["transaction"]
    statements: Annotated[list[RpcStatement], Field(min_length=1, max_length=100)]


class RpcAction(BaseModel):
    op: Literal["action"]
    name: Short
    params: Annotated[dict[Short, SqlValue], Field(max_length=1000)] = {}


class RpcExportAction(BaseModel):
    op: Literal["exportAction"]
    instance: UUID
    export: Short
    name: Short
    params: Annotated[dict[Short, SqlValue], Field(max_length=1000)] = {}


class RpcSearch(BaseModel):
    """The instance's own rows its `homeai.index` declares, by meaning and by words (M22-07)."""

    op: Literal["search"]
    query: Annotated[str, Field(min_length=1, max_length=2000)]
    limit: int = Field(default=10, ge=1, le=50)


RpcRequest = Annotated[
    RpcGetAll | RpcGetFirst | RpcRun | RpcTransaction | RpcAction | RpcExportAction | RpcSearch,
    Field(discriminator="op"),
]


class ExportBody(BaseModel):
    # A new folder; default Exports/<slug>-<UTC time> in the instance's space.
    folder: VPath | None = None


class ExportTableOut(BaseModel):
    name: str
    file: str
    rows: int


class ExportOut(BaseModel):
    folder: str
    tables: list[ExportTableOut]


class ImportBody(BaseModel):
    table: Short
    path: VPath
    # CSV header -> table column; without one, headers match columns by name.
    mapping: Annotated[dict[Short, Short], Field(max_length=256)] | None = None
    delimiter: Literal[",", ";", "\t"] = ","
    dry_run: bool = False


class ImportOut(BaseModel):
    table: str
    rows: int
    mapping: dict[str, str]
    skipped: list[str]
    # Without a mapping, headers that match no column (a dry run reports them; an import fails).
    unmatched: list[str]
    headers: list[str]
    columns: list[str]
    dry_run: bool


class MigrationStepOut(BaseModel):
    kind: Literal["additive", "safe", "destructive"]
    op: str
    table: str
    sql: list[str]
    reason: str


class MigrationOut(BaseModel):
    # None when the schema is already up to date (nothing is recorded).
    id: UUID | None
    instance_id: UUID
    status: Literal["up_to_date", "applied", "pending", "failed", "rejected", "superseded"]
    steps: list[MigrationStepOut]
    summary: dict[str, int]
    needs_approval: bool
    snapshot: str | None
    error: str | None
    created_by: UUID | None
    created_at: datetime | None
    decided_by: UUID | None
    decided_at: datetime | None


class MigrationList(BaseModel):
    migrations: list[MigrationOut]


class InstanceMigrationOut(BaseModel):
    instance_id: UUID
    # None when it couldn't be planned (`error` says why).
    migration: MigrationOut | None
    error: str | None


class UpdateInstanceOut(BaseModel):
    instance: InstanceOut
    migration: MigrationOut


class SystemActionOut(BaseModel):
    name: str
    description: str
    params: dict[str, Any]


class SystemAppOut(BaseModel):
    slug: str
    name: str
    version: str
    icon: str
    description: str
    native: bool
    read_only_source: bool
    privileged: list[str]
    actions: list[SystemActionOut]
    agent_md: str


class SystemAppList(BaseModel):
    apps: list[SystemAppOut]


class SystemActionRequest(BaseModel):
    params: dict[str, str | int | float | bool | None] = {}


class SystemActionResult(BaseModel):
    ok: bool = True
    result: dict[str, Any]


ToolName = Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")]
ToolPermission = Literal["ask", "allow"]


class AgentToolIn(BaseModel):
    name: ToolName
    label: Annotated[str, Field(min_length=1, max_length=80)]
    description: Annotated[str, Field(max_length=500)]
    group: Annotated[str, Field(min_length=1, max_length=40)]
    read_only: bool


class ToolCatalogIn(BaseModel):
    tools: Annotated[list[AgentToolIn], Field(max_length=200)]


RoutineKey = Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")]


class DeliveredRoutineIn(BaseModel):
    key: RoutineKey
    name: Annotated[str, Field(min_length=1, max_length=80)]
    description: Annotated[str, Field(max_length=500)]


class DeliveredRoutineCatalogIn(BaseModel):
    routines: Annotated[list[DeliveredRoutineIn], Field(max_length=50)]


class DeliveredRoutineOut(BaseModel):
    key: str
    name: str
    description: str
    enabled_by_admin: bool


class DeliveredRoutineList(BaseModel):
    routines: list[DeliveredRoutineOut]


class DeliveredRoutineAdminIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool


class UserToolOut(BaseModel):
    name: str
    label: str
    description: str
    group: str
    read_only: bool
    enabled: bool
    permission: ToolPermission
    enabled_locked: bool
    permission_locked: bool


class UserToolList(BaseModel):
    tools: list[UserToolOut]


class UserToolChange(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: ToolName
    enabled: bool | None = None
    permission: ToolPermission | None = None


class UserToolChanges(BaseModel):
    changes: Annotated[list[UserToolChange], Field(min_length=1, max_length=200)]


class AdminToolOut(BaseModel):
    name: str
    label: str
    description: str
    group: str
    read_only: bool
    enabled: bool
    ceiling: ToolPermission


class AdminToolList(BaseModel):
    tools: list[AdminToolOut]


class AdminToolChange(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: ToolName
    enabled: bool | None = None
    ceiling: ToolPermission | None = None


class AdminToolChanges(BaseModel):
    changes: Annotated[list[AdminToolChange], Field(min_length=1, max_length=200)]


class ToolPolicyEntry(BaseModel):
    available: bool
    permission: ToolPermission
    can_always_allow: bool


class ToolPolicyOut(BaseModel):
    tools: dict[str, ToolPolicyEntry]


# --- integrations (M18-03, app.core.integrations) -------------------------------------

IntegrationSlug = Annotated[str, Field(pattern=r"^[a-z][a-z0-9-]{0,39}$")]
McpName = Annotated[str, Field(pattern=r"^[A-Za-z0-9_.\-]{1,128}$")]
CredentialModel = Literal["per_user", "shared", "none"]
AuthMethod = Literal["none", "token", "oauth"]
ConnectionStatus = Literal["ready", "connected", "not_connected", "needs_reconnecting"]
Fingerprint = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
# Empty or "Authorization": `Authorization: Bearer <token>`.
TokenHeader = Annotated[str, Field(pattern=r"^[A-Za-z0-9-]{1,64}$")]
MAX_JSON_FIELD = 64 * 1024


def _small_json(value: Any) -> Any:
    if len(json.dumps(value)) > MAX_JSON_FIELD:
        raise ValueError("too large")
    return value


SmallJson = Annotated[dict[str, Any], AfterValidator(_small_json)]


class IntegrationInstall(BaseModel):
    model_config = ConfigDict(extra="forbid")

    slug: IntegrationSlug
    name: Annotated[str, Field(min_length=1, max_length=80)]
    url: Annotated[str, Field(min_length=1, max_length=2000)]
    description: Annotated[str, Field(max_length=500)] = ""
    credential_model: CredentialModel
    auth_method: AuthMethod
    cert_fingerprint: Fingerprint | None = None
    private_address: bool = False
    token_header: TokenHeader | None = None


class IntegrationPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    slug: IntegrationSlug | None = None
    name: Annotated[str, Field(min_length=1, max_length=80)] | None = None
    url: Annotated[str, Field(min_length=1, max_length=2000)] | None = None
    description: Annotated[str, Field(max_length=500)] | None = None
    credential_model: CredentialModel | None = None
    auth_method: AuthMethod | None = None
    cert_fingerprint: Fingerprint | None = None
    private_address: bool | None = None
    token_header: TokenHeader | None = None
    # Off: kept as is (credentials, tool settings) but unusable until turned on.
    turned_off: bool | None = None


class IntegrationToolOut(BaseModel):
    name: str
    title: str | None
    description: str
    annotations: dict[str, Any]
    enabled: bool
    permission: ToolPermission
    enabled_locked: bool
    permission_locked: bool


class OAuthSummary(BaseModel):
    """What an OAuth server's metadata said (docs/INTEGRATIONS.md §5.1).

    Not `revocable`: disconnecting can't sign HomeAI out at the provider;
    the user removes it in the provider's own settings too.
    """

    issuer: Annotated[str, Field(max_length=2000)] | None = None
    sign_in_host: Annotated[str, Field(max_length=253)] | None = None
    token_host: Annotated[str, Field(max_length=253)] | None = None
    registration: Literal["dynamic", "client_id_url", "unsupported"] | None = None
    revocable: bool = False


class OAuthCheck(OAuthSummary):
    """The check's look at the sign-in; `problem` when it can't be used (`oauth_unsupported`, ...)."""

    problem: Annotated[str, Field(max_length=40)] | None = None
    detail: Annotated[str, Field(max_length=1000)] | None = None


class OAuthStartOut(BaseModel):
    """The provider's sign-in page, to open in a browser."""

    authorize_url: str


class OAuthDiscoveredIn(BaseModel):
    """The gateway read a server's OAuth metadata: its summary and the endpoints it uses."""

    model_config = ConfigDict(extra="forbid")

    summary: OAuthSummary | None = None
    endpoints: Annotated[list[Annotated[str, Field(max_length=2000)]], Field(max_length=8)]


class RefreshedCredentialIn(BaseModel):
    """The gateway refreshed an OAuth token; the new ciphertext replaces the stored one."""

    model_config = ConfigDict(extra="forbid")

    user_id: UUID | None = None
    ciphertext: Annotated[str, Field(min_length=1, max_length=32_000)]


class IntegrationOut(BaseModel):
    id: UUID
    slug: str
    name: str
    description: str
    credential_model: CredentialModel
    auth_method: AuthMethod
    # A credential for it would cross the network unencrypted: warn before connecting.
    plain_http: bool
    oauth: OAuthSummary | None = None
    turned_off: bool = False
    status: ConnectionStatus
    tools: list[IntegrationToolOut]


class IntegrationList(BaseModel):
    integrations: list[IntegrationOut]


class IntegrationToolChange(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: McpName
    enabled: bool | None = None
    permission: ToolPermission | None = None


class IntegrationToolChanges(BaseModel):
    changes: Annotated[list[IntegrationToolChange], Field(min_length=1, max_length=500)]


class AdminIntegrationToolChange(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: McpName
    enabled: bool | None = None
    ceiling: ToolPermission | None = None


class AdminIntegrationToolChanges(BaseModel):
    changes: Annotated[list[AdminIntegrationToolChange], Field(min_length=1, max_length=500)]


class CredentialIn(BaseModel):
    """A token or API key, on its way to the gateway to be encrypted. Never stored as is."""

    model_config = ConfigDict(extra="forbid")

    # Pasted, so surrounding whitespace goes; it becomes a header value, so no line breaks.
    secret: Annotated[
        str,
        StringConstraints(
            strip_whitespace=True, min_length=1, max_length=8192, pattern=r"^[\x20-\x7e]+$"
        ),
    ]


class ConnectionOut(BaseModel):
    """A stored credential, and whether the server accepted it when it was entered.

    `checked` is false when the server couldn't be asked (`problem` says
    why, e.g. `unreachable`); the credential is kept and the first call
    tells. A credential the server refused is never stored
    (`credential_rejected`).
    """

    status: ConnectionStatus
    checked: bool
    problem: str | None = None


class IntegrationCheckOut(BaseModel):
    """The gateway's look at a server, without a credential (Settings → Add integration).

    `auth` is what it wants (`none`: it answered; `token`/`oauth`: it asked
    for sign-in). Otherwise `problem` says why it couldn't be reached:
    `certificate_untrusted` / `certificate_changed` (with the `certificate`
    it presented, to confirm), `address_refused` (a name that points to a
    private address needs the exception), `unreachable`, `timeout`,
    `server_error`, `protocol_error`. `this_machine`: the address is one of
    `HOMEAI_LAN_ADDRESSES` (None: unknown, e.g. not configured or a name).
    """

    reachable: bool
    this_machine: bool | None = None
    auth: AuthMethod | None = None
    problem: Annotated[str, Field(max_length=40)] | None = None
    detail: Annotated[str, Field(max_length=1000)] | None = None
    certificate: Fingerprint | None = None
    protocol_version: Annotated[str, Field(max_length=40)] | None = None
    server: SmallJson | None = None
    oauth: OAuthCheck | None = None


class AdminIntegrationToolOut(BaseModel):
    name: str
    title: str | None
    description: str
    annotations: dict[str, Any]
    enabled: bool
    ceiling: ToolPermission


class IntegrationResourceOut(BaseModel):
    uri: str
    name: str
    title: str | None
    description: str
    mime_type: str | None


class IntegrationPromptOut(BaseModel):
    name: str
    title: str | None
    description: str
    arguments: list[dict[str, Any]]


class IntegrationConnectionOut(BaseModel):
    user_id: UUID
    username: str | None
    display_name: str | None
    status: Literal["connected", "needs_reconnecting"]
    updated_at: datetime


class AdminIntegrationOut(BaseModel):
    id: UUID
    slug: str
    name: str
    description: str
    credential_model: CredentialModel
    auth_method: AuthMethod
    url: str
    plain_http: bool
    oauth: OAuthSummary | None = None
    turned_off: bool = False
    cert_fingerprint: str | None
    private_address: bool
    token_header: str | None
    server_info: dict[str, Any] | None
    protocol_version: str | None
    discovered_at: datetime | None
    installed_by: UUID | None
    created_at: datetime
    # Only for a shared integration: is the household's credential set.
    shared_credential: ConnectionStatus | None
    connections: list[IntegrationConnectionOut]
    tools: list[AdminIntegrationToolOut]
    resources: list[IntegrationResourceOut]
    prompts: list[IntegrationPromptOut]


class AdminIntegrationList(BaseModel):
    integrations: list[AdminIntegrationOut]


class DiscoveredTool(BaseModel):
    name: McpName
    title: Annotated[str, Field(max_length=200)] | None = None
    description: Annotated[str, Field(max_length=10_000)] = ""
    input_schema: SmallJson
    annotations: SmallJson = {}


class DiscoveredResource(BaseModel):
    uri: Annotated[str, Field(min_length=1, max_length=2000)]
    name: Annotated[str, Field(min_length=1, max_length=200)]
    title: Annotated[str, Field(max_length=200)] | None = None
    description: Annotated[str, Field(max_length=10_000)] = ""
    mime_type: Annotated[str, Field(max_length=200)] | None = None


class DiscoveredPrompt(BaseModel):
    name: McpName
    title: Annotated[str, Field(max_length=200)] | None = None
    description: Annotated[str, Field(max_length=10_000)] = ""
    arguments: Annotated[list[SmallJson], Field(max_length=100)] = []


class DiscoveryIn(BaseModel):
    """What the gateway found on a server (`app.core.integrations.store_discovery`)."""

    protocol_version: Annotated[str, Field(max_length=40)] | None = None
    server: SmallJson = {}
    instructions: Annotated[str, Field(max_length=20_000)] = ""
    tools: Annotated[list[DiscoveredTool], Field(max_length=500)] = []
    resources: Annotated[list[DiscoveredResource], Field(max_length=1000)] = []
    prompts: Annotated[list[DiscoveredPrompt], Field(max_length=500)] = []

    @model_validator(mode="after")
    def _unique(self) -> DiscoveryIn:
        for items, key in ((self.tools, "name"), (self.resources, "uri"), (self.prompts, "name")):
            names = [getattr(i, key) for i in items]
            if len(set(names)) != len(names):
                raise ValueError(f"duplicate {key}")
        return self


class IntegrationGrantRequest(BaseModel):
    delegation: str
    integration: IntegrationSlug


class IntegrationGrantCredential(BaseModel):
    user_id: UUID | None
    ciphertext: str
    status: Literal["connected", "needs_reconnecting"]


class IntegrationGatewayView(BaseModel):
    id: UUID
    slug: str
    url: str
    auth_method: AuthMethod
    credential_model: CredentialModel
    cert_fingerprint: str | None
    private_address: bool
    token_header: str | None
    protocol_version: str | None


class IntegrationGrantOut(BaseModel):
    user_id: UUID
    session_id: UUID
    thread_id: str | None
    integration: IntegrationGatewayView
    turned_off: bool = False
    status: ConnectionStatus
    credential: IntegrationGrantCredential | None
    tools: dict[str, ToolPolicyEntry]


class CredentialStatusIn(BaseModel):
    """The gateway found a credential rejected: a user's (`user_id`), or the shared one."""

    model_config = ConfigDict(extra="forbid")

    user_id: UUID | None = None
    status: Literal["needs_reconnecting"]


class IntegrationApprovalRequest(BaseModel):
    """agent-server, after the user approved this exact call on its card."""

    delegation: Secret
    integration: IntegrationSlug
    tool: McpName
    arguments: SmallJson


class IntegrationApprovalConsume(BaseModel):
    """The gateway, about to run a call that asks first."""

    delegation: Secret
    token: Secret
    integration: IntegrationSlug
    tool: McpName
    arguments: SmallJson


class IntegrationAuditIn(BaseModel):
    """One gateway action, recorded in the platform's audit log (docs/INTEGRATIONS.md §11).

    The gateway leaves secrets out of `arguments` before sending them.
    """

    delegation: Secret
    integration: IntegrationSlug
    action: Literal["open", "call", "read", "prompt"]
    target: Annotated[str, Field(max_length=2000)] = ""
    arguments: SmallJson = {}
    context: Literal["chat", "routine", "app"] | None = None
    approval: Literal["allowed", "approved"] | None = None
    status: Literal["ok", "error"]
    error_code: Annotated[str, Field(max_length=40)] | None = None

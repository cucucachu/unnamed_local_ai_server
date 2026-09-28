"""Request/response bodies for `/api/auth/*` and `/api/platform/*`.

Documented field-by-field in docs/ARCHITECTURE.md §3 "Platform API"; keep
the two in sync. String caps here only bound payload size - the real rules
(username format, password length, ...) live in `app/core/passwords.py` so
they produce specific error codes.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

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


class GrepBody(BaseModel):
    pattern: str = Field(min_length=1, max_length=4096)
    path: VPath | None = None
    glob: Short | None = None
    max_count: int | None = Field(default=None, ge=0)


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


RpcRequest = Annotated[
    RpcGetAll | RpcGetFirst | RpcRun | RpcTransaction | RpcAction | RpcExportAction,
    Field(discriminator="op"),
]


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

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
    disabled_at: datetime | None
    created_at: datetime


class SessionResponse(BaseModel):
    user: UserOut
    session_token: str | None = None


class StatusResponse(BaseModel):
    setup_required: bool
    authenticated: bool
    user: UserOut | None = None


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


class InstanceAppOut(BaseModel):
    id: UUID
    slug: str
    name: str
    version: str | None
    icon: str | None


class InstanceOut(BaseModel):
    id: UUID
    app_id: UUID
    space_id: UUID
    # "working", or the pinned published version's id.
    tracks: str
    installed_by: UUID | None
    granted_permissions: dict[str, Any]
    created_at: datetime
    app: InstanceAppOut


class InstanceList(BaseModel):
    instances: list[InstanceOut]


class InstallRequest(BaseModel):
    app_id: UUID
    tracks: Short = "working"

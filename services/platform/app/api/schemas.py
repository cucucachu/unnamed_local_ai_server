"""Request/response bodies for `/api/auth/*` and `/api/platform/*`.

Documented field-by-field in docs/ARCHITECTURE.md §3 "Platform API"; keep
the two in sync. String caps here only bound payload size - the real rules
(username format, password length, ...) live in `app/core/passwords.py` so
they produce specific error codes.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal
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

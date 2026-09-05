"""REST settings document — `/api/settings` (M8-02).

Contract added to `docs/ARCHITECTURE.md` §3 "HTTP API" alongside the other
endpoints there — do not deviate from the shapes below.

`GET` always returns the full document (defaults filled in for anything not
yet stored, per `app/db/settings.py::SettingsDocument`). `PUT` accepts a
PARTIAL document (any subset of the three fields) and validates strictly:
`SettingsUpdateBody`'s `model_config = ConfigDict(extra="forbid")` makes an
unknown extra key a `422` (FastAPI's own request-body validation, same as
any other pydantic model here), and a wrong type / invalid
`edit_mode_default` literal value is likewise a normal pydantic `422` —
no custom validation code needed for either case.
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict

from app.db.settings import SettingsDocument, SettingsStore

router = APIRouter()


class SettingsUpdateBody(BaseModel):
    """A PARTIAL settings document — every field optional, `None` means
    "not provided" (rather than "set to null"), and any field not among
    `SettingsDocument`'s own three is a `422` via `extra="forbid"`.

    `edit_mode_default`'s `Literal["truncate", "fork"]` is duplicated from
    `SettingsDocument` (rather than referencing it) since it's the simplest
    way to keep this field truly optional while still 422-ing on any value
    outside the two allowed literals.

    `strict=True`: pydantic's default ("lax") mode coerces some non-bool
    JSON values (e.g. the string `"yes"`) into `bool` rather than rejecting
    them — not what the ticket's "422 on wrong type (e.g. `hitl_enabled:
    "yes"`)" spec wants. Strict mode requires an actual JSON `true`/`false`
    for a `bool` field, which is what a real client sends anyway.
    """

    model_config = ConfigDict(extra="forbid", strict=True)

    hitl_enabled: bool | None = None
    thinking_enabled: bool | None = None
    edit_mode_default: Literal["truncate", "fork"] | None = None


def _settings_store(request: Request) -> SettingsStore:
    return request.app.state.settings_store


@router.get("/settings", response_model=SettingsDocument)
async def get_settings(request: Request) -> SettingsDocument:
    store = _settings_store(request)
    return await store.get_document()


@router.put("/settings", response_model=SettingsDocument)
async def update_settings(body: SettingsUpdateBody, request: Request) -> SettingsDocument:
    store = _settings_store(request)
    # `exclude_unset=True`: only fields the caller actually sent are merged
    # (a field explicitly omitted from the request body stays untouched in
    # storage, matching the "PUT accepts a PARTIAL document" spec) — a field
    # sent as an explicit `null` would be excluded too, but none of the
    # three fields are nullable in `SettingsDocument`, so that's already
    # rejected as a `422` type error before reaching here.
    partial = body.model_dump(exclude_unset=True)
    return await store.update_document(partial)

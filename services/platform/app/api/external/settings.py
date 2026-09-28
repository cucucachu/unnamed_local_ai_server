"""`GET /api/platform/settings`: any authenticated principal (incl. agent).

The flag is also on `GET /api/auth/status` (unauthenticated) so the login
UI can hide the password. PATCH is on the admin router (stepped-up admin
+ privileged origin).
"""

from fastapi import APIRouter, Request

from app.api.schemas import PlatformSettingsOut
from app.core import webauthn
from app.core.principal import CurrentUser

router = APIRouter()


def settings_out(request: Request) -> PlatformSettingsOut:
    return PlatformSettingsOut(
        public_https=bool(getattr(request.app.state, "public_https", False)),
        domain_configured=webauthn.rp_id(request.app.state.settings) is not None,
    )


@router.get("/settings", response_model=PlatformSettingsOut)
async def get_settings(request: Request, _principal: CurrentUser) -> PlatformSettingsOut:
    return settings_out(request)

"""`/api/platform/system-apps*`: image-shipped Chat, Files, Settings, Home (M14-03).

Read by any authenticated user (including an agent delegation). Actions
reuse the files API's authorization (write on both spaces for a move/copy).
"""

from typing import Any

from fastapi import APIRouter, Request

from app.api.schemas import SystemActionRequest, SystemActionResult, SystemAppList, SystemAppOut
from app.core import system_apps
from app.core.errors import NotFound
from app.core.principal import CurrentUser

router = APIRouter(prefix="/system-apps")


def _root(request: Request):
    return request.app.state.settings.system_apps_dir


@router.get("", response_model=SystemAppList)
async def list_system_apps(request: Request, principal: CurrentUser) -> SystemAppList:
    del principal
    apps = [
        SystemAppOut(**system_apps.as_dict(a)) for a in system_apps.load_system_apps(_root(request))
    ]
    return SystemAppList(apps=apps)


@router.get("/{slug}", response_model=SystemAppOut)
async def get_system_app(slug: str, request: Request, principal: CurrentUser) -> SystemAppOut:
    del principal
    app = system_apps.get_system_app(_root(request), slug)
    if app is None:
        raise NotFound("not_found")
    return SystemAppOut(**system_apps.as_dict(app))


@router.post("/{slug}/actions/{name}", response_model=SystemActionResult)
async def run_system_action(
    slug: str,
    name: str,
    body: SystemActionRequest,
    request: Request,
    principal: CurrentUser,
) -> SystemActionResult:
    result: dict[str, Any] = await system_apps.run_action(
        request, principal, _root(request), slug, name, dict(body.params)
    )
    return SystemActionResult(result=result)

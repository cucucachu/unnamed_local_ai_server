from fastapi import APIRouter

from app.api.external import (
    admin,
    appdata,
    apps,
    audit,
    device_pairs,
    directory,
    files,
    me,
    media,
    settings,
    spaces,
    system_apps,
    wireguard,
)

router = APIRouter(prefix="/api/platform")
router.include_router(me.router)
router.include_router(wireguard.router)
router.include_router(device_pairs.router)
router.include_router(directory.router)
router.include_router(spaces.router)
router.include_router(system_apps.router)
router.include_router(apps.router)
router.include_router(appdata.router)
router.include_router(files.router)
router.include_router(media.router)
router.include_router(settings.router)
router.include_router(admin.router)
router.include_router(audit.router)

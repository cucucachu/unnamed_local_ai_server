from fastapi import APIRouter

from app.api.external import admin, directory, files, me, media, spaces

router = APIRouter(prefix="/api/platform")
router.include_router(me.router)
router.include_router(directory.router)
router.include_router(spaces.router)
router.include_router(files.router)
router.include_router(media.router)
router.include_router(admin.router)

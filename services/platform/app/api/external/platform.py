from fastapi import APIRouter

from app.api.external import admin, me

router = APIRouter(prefix="/api/platform")
router.include_router(me.router)
router.include_router(admin.router)

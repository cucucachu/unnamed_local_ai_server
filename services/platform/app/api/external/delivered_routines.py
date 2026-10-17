"""`/api/platform/delivered-routines` (M21-08): the admin's switch for each delivered routine.

Readable by anyone signed in and by their agent; the admin changes it on
the admin router (`PUT /admin/delivered-routines/{key}`).
"""

from fastapi import APIRouter, Request

from app.api.schemas import DeliveredRoutineList
from app.core import delivered_routines
from app.core.principal import CurrentUser

router = APIRouter(prefix="/delivered-routines")


@router.get("", response_model=DeliveredRoutineList)
async def read(request: Request, principal: CurrentUser) -> DeliveredRoutineList:
    async with request.app.state.db_pool.connection() as conn:
        return DeliveredRoutineList(routines=await delivered_routines.view(conn))

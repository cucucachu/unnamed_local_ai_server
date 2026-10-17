from fastapi import APIRouter, Depends, Request, status

from app.api.internal.service_auth import AGENT, require_service
from app.api.schemas import DeliveredRoutineCatalogIn, DeliveredRoutineList
from app.core import delivered_routines

router = APIRouter(dependencies=[Depends(require_service(AGENT))])


@router.put("/delivered-routines", status_code=status.HTTP_204_NO_CONTENT)
async def replace(body: DeliveredRoutineCatalogIn, request: Request) -> None:
    """agent-server's delivered routines, sent at its startup next to its tool catalog."""
    routines = [delivered_routines.Routine(r.key, r.name, r.description) for r in body.routines]
    async with request.app.state.db_pool.connection() as conn:
        await delivered_routines.replace_catalog(conn, routines)


@router.get("/delivered-routines", response_model=DeliveredRoutineList)
async def read(request: Request) -> DeliveredRoutineList:
    """The admin's switches, for agent-server's scheduler (it has no user to ask as)."""
    async with request.app.state.db_pool.connection() as conn:
        return DeliveredRoutineList(routines=await delivered_routines.view(conn))

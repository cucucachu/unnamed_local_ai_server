"""`/api/platform/users/directory`: who can be added to a space (member pickers)."""

from fastapi import APIRouter, Request

from app.api.schemas import Directory
from app.core import users
from app.core.principal import HumanUser

router = APIRouter(prefix="/users")


@router.get("/directory", response_model=Directory)
async def directory(request: Request, principal: HumanUser):
    async with request.app.state.db_pool.connection() as conn:
        return Directory(users=await users.list_directory(conn))

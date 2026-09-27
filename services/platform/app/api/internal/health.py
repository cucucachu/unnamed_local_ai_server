import psycopg
from fastapi import APIRouter, Request, Response, status

router = APIRouter()


@router.get("/health")
async def health(request: Request, response: Response) -> dict[str, str]:
    """Liveness + database reachability; used by the compose healthcheck."""
    try:
        async with request.app.state.db_pool.connection(timeout=3) as conn:
            await conn.execute("SELECT 1")
    except psycopg.Error:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return {"status": "degraded", "database": "unreachable"}
    return {"status": "ok"}

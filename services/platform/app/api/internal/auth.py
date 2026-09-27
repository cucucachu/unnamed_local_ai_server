from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse

from app.api.session_http import session_token
from app.core import sessions
from app.core.principal import IDENTITY_HEADER, issue_identity_token

router = APIRouter(prefix="/auth")


@router.get("/verify")
async def verify(request: Request) -> Response:
    """Caddy `forward_auth` target: the original request's cookie/bearer in, identity JWT out.

    Unauthenticated by design (it *is* the authentication). One indexed
    query per call; also slides the session's expiry.
    """
    token = session_token(request)
    row = None
    if token is not None:
        async with request.app.state.db_pool.connection() as conn:
            row = await sessions.resolve_token(conn, token)
    if row is None:
        return JSONResponse({"detail": "unauthenticated"}, status_code=401)
    identity = issue_identity_token(
        request.app.state.tokens, user_id=row["id"], session_id=row["session_id"], role=row["role"]
    )
    return Response(status_code=200, headers={IDENTITY_HEADER: identity})

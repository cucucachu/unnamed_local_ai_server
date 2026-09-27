from typing import Any

from fastapi import APIRouter, Request

router = APIRouter()


@router.get("/jwks")
async def jwks(request: Request) -> dict[str, Any]:
    """Public signing keys (RFC 7517 JWK Set). Unauthenticated by design."""
    return request.app.state.tokens.jwks()

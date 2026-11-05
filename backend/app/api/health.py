from fastapi import APIRouter
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.core.config import get_settings
from app.core.db import connection

router = APIRouter(tags=["health"])


@router.get("/health")
async def liveness() -> dict[str, str]:
    """Process is up. Does not touch the database."""
    s = get_settings()
    return {"status": "ok", "env": s.app_env, "version": s.git_sha}


@router.get("/health/db")
async def readiness() -> dict[str, str] | JSONResponse:
    """Database reachable through the pooled connection as the app role."""
    try:
        async with connection() as conn:
            await conn.execute(text("SELECT 1"))
    except Exception:
        return JSONResponse({"status": "not ok"}, status_code=503)
    return {"status": "ok"}

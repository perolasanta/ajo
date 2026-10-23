from fastapi import APIRouter
from sqlalchemy import text

from app.core.config import get_settings
from app.core.db import get_engine

router = APIRouter(tags=["health"])


@router.get("/health")
async def liveness() -> dict[str, str]:
    """Process is up. Does not touch the database."""
    s = get_settings()
    return {"status": "ok", "env": s.app_env, "version": s.git_sha}


@router.get("/health/db")
async def readiness() -> dict[str, str]:
    """Database reachable through the pooled connection as the app role."""
    async with get_engine().connect() as conn:
        row = (
            await conn.execute(text("SELECT current_user, current_setting('server_version')"))
        ).one()
    return {"status": "ok", "db_user": row[0], "server_version": row[1]}

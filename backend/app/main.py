from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from time import perf_counter

import structlog
from fastapi import FastAPI, Request
from starlette.middleware.base import RequestResponseEndpoint
from starlette.responses import Response

from app.api import (
    auth,
    consent,
    disbursements,
    health,
    loans,
    members,
    payroll,
    policies,
    repayments,
)
from app.core.config import get_settings
from app.core.db import check_application_role, dispose_engine
from app.core.errors import install_error_handlers
from app.core.logging import configure_logging

access_log = structlog.get_logger("ajo.access")


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    configure_logging(settings.log_level)
    await check_application_role(settings.database_url)
    yield
    await dispose_engine()


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="Ajo API",
        version="0.1.0",
        lifespan=lifespan,
        docs_url=None if settings.is_production else "/api/docs",
        openapi_url=None if settings.is_production else "/api/openapi.json",
        redoc_url=None if settings.is_production else "/redoc",
    )
    install_error_handlers(app)

    @app.middleware("http")
    async def request_timing_and_security_headers(
        request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        started = perf_counter()
        status_code = 500
        try:
            response = await call_next(request)
            status_code = response.status_code
            if request.url.path.startswith("/api/v1/confirm/"):
                response.headers["Cache-Control"] = "no-store"
                response.headers["Referrer-Policy"] = "no-referrer"
            return response
        finally:
            route = request.scope.get("route")
            path = getattr(route, "path", "/<unmatched>")
            duration_ms = round((perf_counter() - started) * 1000, 2)
            access_log.info(
                "request_complete",
                method=request.method,
                path=path,
                status_code=status_code,
                duration_ms=duration_ms,
                slow=duration_ms >= 300,
            )

    app.include_router(health.router, prefix="/api/v1")
    app.include_router(auth.router, prefix="/api/v1")
    app.include_router(policies.router, prefix="/api/v1")
    app.include_router(members.router, prefix="/api/v1")
    app.include_router(loans.router, prefix="/api/v1")
    app.include_router(consent.router, prefix="/api/v1")
    app.include_router(disbursements.router, prefix="/api/v1")
    app.include_router(repayments.router, prefix="/api/v1")
    app.include_router(payroll.router, prefix="/api/v1")
    return app


app = create_app()

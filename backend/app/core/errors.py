"""Typed application errors. Never leak other members' data in messages."""

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse


class AppError(Exception):
    status_code = 400
    code = "app_error"

    def __init__(self, message: str = "Request could not be completed") -> None:
        super().__init__(message)
        self.message = message


class NotFound(AppError):
    status_code = 404
    code = "not_found"


class Forbidden(AppError):
    status_code = 403
    code = "forbidden"


class Conflict(AppError):
    status_code = 409
    code = "conflict"


class TooManyRequests(AppError):
    status_code = 429
    code = "rate_limited"


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def _handle(_: Request, exc: AppError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code, content={"error": exc.code, "message": exc.message}
        )

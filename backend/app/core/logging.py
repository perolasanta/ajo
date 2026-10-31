"""Structured logging that redacts secrets and personal data."""

import logging
import re

import structlog
from structlog.typing import EventDict, WrappedLogger

_REDACT = {
    "password",
    "pin",
    "otp",
    "token",
    "secret",
    "authorization",
    "cookie",
    "account_number",
    "bvn",
    "nin",
    "salary",
    "database_url",
}


class ConsentTokenPathFilter(logging.Filter):
    _token_path = re.compile(r"(/api/v1/confirm/[^/\s?]+/)[^/\s?]+")

    def filter(self, record: logging.LogRecord) -> bool:
        if record.name == "uvicorn.access":
            message = record.getMessage()
            redacted = self._token_path.sub(r"\1[redacted]", message)
            if redacted != message:
                record.msg = redacted
                record.args = ()
        return True


def _redact(_: WrappedLogger, __: str, event_dict: EventDict) -> EventDict:
    for key in list(event_dict):
        if any(part in key.lower() for part in _REDACT):
            event_dict[key] = "[redacted]"
    return event_dict


def configure_logging(level: str = "INFO") -> None:
    logging.basicConfig(level=level, format="%(message)s")
    access_logger = logging.getLogger("uvicorn.access")
    if not any(
        isinstance(log_filter, ConsentTokenPathFilter) for log_filter in access_logger.filters
    ):
        access_logger.addFilter(ConsentTokenPathFilter())
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            _redact,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(logging.getLevelName(level)),
    )

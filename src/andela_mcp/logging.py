from __future__ import annotations

import logging
import sys
from typing import Any, cast

import structlog
from structlog.types import EventDict, Processor

from andela_mcp.config import Settings


def _gcp_severity(_: Any, __: str, event_dict: EventDict) -> EventDict:
    """Map structlog levels to Google Cloud Logging severity field."""
    level = event_dict.pop("level", None)
    if level:
        event_dict["severity"] = level.upper()
    return event_dict


def configure_logging(settings: Settings) -> None:
    """Configure structlog + stdlib logging once, at startup.

    Cloud Run captures stdout as structured logs when emitted as JSON; the
    `severity` field is recognized by Cloud Logging.
    """
    timestamper = structlog.processors.TimeStamper(fmt="iso", utc=True)

    shared_processors: list[Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        timestamper,
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]

    if settings.log_format == "json":
        renderer: Processor = structlog.processors.JSONRenderer()
        shared_processors.append(_gcp_severity)
    else:
        renderer = structlog.dev.ConsoleRenderer(colors=sys.stderr.isatty())

    structlog.configure(
        processors=[*shared_processors, renderer],
        wrapper_class=structlog.make_filtering_bound_logger(
            logging.getLevelNamesMapping()[settings.log_level]
        ),
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )

    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=settings.log_level,
    )

    for noisy in ("httpx", "httpcore", "uvicorn.access"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    return cast("structlog.stdlib.BoundLogger", structlog.get_logger(name))

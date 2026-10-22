"""Structured logging: JSON lines everywhere, a pretty console renderer when ``SPM_ENV=local``.

``request_id`` and ``correlation_id`` are bound as structlog context vars by the request
middleware, so they appear in every log line emitted while a request is being handled.
"""

import logging
import sys

import structlog
from structlog.typing import Processor

from spm.config import Settings


def configure_logging(settings: Settings, level: int = logging.INFO) -> None:
    shared: list[Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
    ]
    renderer: Processor
    if settings.env == "local":
        renderer = structlog.dev.ConsoleRenderer()
    else:
        # No frame locals: they would put request headers (bearer tokens) into the logs.
        shared.append(
            structlog.processors.ExceptionRenderer(
                structlog.tracebacks.ExceptionDictTransformer(show_locals=False)
            )
        )
        renderer = structlog.processors.JSONRenderer()

    structlog.configure(
        processors=[*shared, structlog.stdlib.ProcessorFormatter.wrap_for_formatter],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    # Route stdlib loggers (uvicorn, sqlalchemy, …) through the same renderer.
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        structlog.stdlib.ProcessorFormatter(
            foreign_pre_chain=shared,
            processors=[structlog.stdlib.ProcessorFormatter.remove_processors_meta, renderer],
        )
    )
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)
    for name in ("uvicorn", "uvicorn.error"):
        logging.getLogger(name).handlers.clear()
        logging.getLogger(name).propagate = True
    # RequestIdMiddleware already logs one line per request, with ids; silence the duplicate.
    access = logging.getLogger("uvicorn.access")
    access.handlers.clear()
    access.propagate = False
    logging.getLogger("watchfiles").setLevel(logging.WARNING)

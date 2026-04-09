"""Shared structured-logging configuration.

Call ``configure_logging(service_name)`` once at application startup to
set up ``structlog`` with JSON rendering, automatic ``trace_id`` binding
from context-vars, and stdlib-logging interop.

Usage::

    from mas_core.observability.logging import configure_logging
    configure_logging("orchestrator-api")
"""

from __future__ import annotations

import logging
import sys

import structlog


def configure_logging(
    service_name: str,
    *,
    level: int = logging.INFO,
    json: bool = True,
) -> None:
    """Configure ``structlog`` for a MAS micro-service.

    Parameters
    ----------
    service_name:
        Injected as ``service`` into every log event.
    level:
        Root logging level (default ``INFO``).
    json:
        If *True* (default / production), use ``JSONRenderer``.
        If *False* (dev), use ``ConsoleRenderer`` for human-readable output.
    """
    shared_processors: list[structlog.types.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        structlog.processors.UnicodeDecoder(),
        _add_service_name(service_name),
    ]

    renderer: structlog.types.Processor = (
        structlog.processors.JSONRenderer() if json else structlog.dev.ConsoleRenderer()
    )

    structlog.configure(
        processors=[
            *shared_processors,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    formatter = structlog.stdlib.ProcessorFormatter(
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            renderer,
        ],
    )

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)

    # Quieten noisy third-party loggers
    for name in ("uvicorn", "uvicorn.access", "httpx", "httpcore"):
        logging.getLogger(name).setLevel(logging.WARNING)


def _add_service_name(service_name: str) -> structlog.types.Processor:
    """Return a processor that injects ``service`` into every event dict."""

    def processor(
        logger: structlog.types.WrappedLogger,
        method_name: str,
        event_dict: structlog.types.EventDict,
    ) -> structlog.types.EventDict:
        event_dict.setdefault("service", service_name)
        return event_dict

    return processor

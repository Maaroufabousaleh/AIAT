"""Trace-ID generation and propagation via structlog context variables.

At every task entry point (API request, cross-team message) call
``bind_trace_id()`` to put the ``trace_id`` into the ``structlog``
context-var store.  All subsequent log lines in the same async context
will include it automatically.

Usage::

    from mas_core.observability.tracing import bind_trace_id, new_trace_id

    # At an API endpoint:
    tid = new_trace_id()
    bind_trace_id(tid)
    logger.info("project_created", project_id=pid)
    # → {"event": "project_created", "trace_id": "<uuid>", ...}
"""

from __future__ import annotations

import uuid

import structlog


def new_trace_id() -> str:
    """Generate a new trace-ID (UUID4 hex string)."""
    return uuid.uuid4().hex


def bind_trace_id(trace_id: str, *, span_id: str | None = None) -> None:
    """Bind ``trace_id`` (and optionally ``span_id``) into the structlog
    context-var store so every subsequent log line includes them.
    """
    ctx: dict[str, str] = {"trace_id": trace_id}
    if span_id:
        ctx["span_id"] = span_id
    structlog.contextvars.bind_contextvars(**ctx)


def clear_trace_context() -> None:
    """Remove trace context vars (call at end of request / message handling)."""
    structlog.contextvars.unbind_contextvars("trace_id", "span_id")

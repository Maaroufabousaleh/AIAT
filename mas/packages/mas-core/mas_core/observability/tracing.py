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

import re
import uuid
from contextvars import ContextVar

import structlog

_TRACE_ID: ContextVar[str | None] = ContextVar("aiat_trace_id", default=None)
_SPAN_ID: ContextVar[str | None] = ContextVar("aiat_span_id", default=None)
_TRACE_HEADER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_TRACEPARENT_RE = re.compile(r"^[0-9a-fA-F]{32}$")
_SPAN_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


def new_trace_id() -> str:
    """Generate a new trace-ID (UUID4 hex string)."""
    return uuid.uuid4().hex


def current_trace_id() -> str | None:
    """Return the trace ID bound to the current async execution context."""

    return _TRACE_ID.get()


def new_span_id() -> str:
    """Generate a bounded span identifier for native evidence rows."""

    return uuid.uuid4().hex[:16]


def current_span_id() -> str | None:
    """Return the span ID bound to the current async execution context."""

    return _SPAN_ID.get()


def is_safe_span_id(value: str | None) -> bool:
    """Return whether a span identifier is safe for a durable query key."""

    return bool(_SPAN_ID_RE.fullmatch(str(value or "").strip()))


def is_safe_trace_id(value: str | None) -> bool:
    """Return whether ``value`` is safe to use as a read/query identifier.

    Request middleware uses :func:`resolve_trace_id` to replace malformed
    values.  Read surfaces must not silently turn a typo into a new trace, so
    they use this predicate and return a bounded validation error instead.
    """

    return bool(_TRACE_HEADER_RE.fullmatch(str(value or "").strip()))


def resolve_trace_id(trace_id: str | None = None, traceparent: str | None = None) -> str:
    """Return a safe caller trace ID or create a fresh root trace.

    AIAT accepts a bounded proprietary header value first and then the
    32-hex trace-id component of a W3C ``traceparent`` value.  Invalid or
    missing values are intentionally replaced rather than logged or echoed.
    """

    supplied = str(trace_id or "").strip()
    if _TRACE_HEADER_RE.fullmatch(supplied):
        return supplied
    parts = str(traceparent or "").strip().split("-")
    if len(parts) >= 2 and _TRACEPARENT_RE.fullmatch(parts[1]):
        return parts[1].lower()
    return new_trace_id()


def bind_trace_id(trace_id: str, *, span_id: str | None = None) -> None:
    """Bind ``trace_id`` (and optionally ``span_id``) into the structlog
    context-var store so every subsequent log line includes them.
    """
    _TRACE_ID.set(trace_id)
    _SPAN_ID.set(span_id if is_safe_span_id(span_id) else None)
    ctx: dict[str, str] = {"trace_id": trace_id}
    if span_id:
        ctx["span_id"] = span_id
    structlog.contextvars.bind_contextvars(**ctx)


def clear_trace_context() -> None:
    """Remove trace context vars (call at end of request / message handling)."""
    _TRACE_ID.set(None)
    _SPAN_ID.set(None)
    structlog.contextvars.unbind_contextvars("trace_id", "span_id")

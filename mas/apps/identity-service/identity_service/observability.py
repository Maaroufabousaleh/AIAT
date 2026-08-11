"""Small payload-free trace helpers for the identity/mail boundary."""

from __future__ import annotations

import re
from uuid import uuid4

TRACE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
SPAN_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


def normalize_trace_id(value: object) -> str | None:
    """Return a safe caller trace ID or ``None``; never echo malformed input."""

    rendered = str(value or "").strip()
    return rendered if TRACE_ID_RE.fullmatch(rendered) else None


def new_span_id() -> str:
    """Generate a short opaque identity-service span ID."""

    return uuid4().hex[:16]


def normalize_span_id(value: object) -> str | None:
    rendered = str(value or "").strip()
    return rendered if SPAN_ID_RE.fullmatch(rendered) else None


__all__ = ["normalize_span_id", "normalize_trace_id", "new_span_id"]

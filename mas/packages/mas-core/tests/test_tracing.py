from __future__ import annotations

from mas_core.observability.tracing import (
    bind_trace_id,
    clear_trace_context,
    current_span_id,
    current_trace_id,
    new_span_id,
    resolve_trace_id,
)


def test_trace_context_round_trip_and_clear() -> None:
    clear_trace_context()
    assert current_trace_id() is None


def test_resolve_trace_id_prefers_safe_header_and_replaces_invalid_values() -> None:
    assert resolve_trace_id("safe-header-123") == "safe-header-123"
    assert resolve_trace_id(
        None,
        "00-abcdef0123456789abcdef0123456789-0123456789abcdef-01",
    ) == "abcdef0123456789abcdef0123456789"
    generated = resolve_trace_id("bad trace value")
    assert generated != "bad trace value"
    assert len(generated) == 32

    bind_trace_id("request-123", span_id="span-1")
    assert current_trace_id() == "request-123"
    assert current_span_id() == "span-1"
    assert len(new_span_id()) == 16

    clear_trace_context()
    assert current_trace_id() is None
    assert current_span_id() is None

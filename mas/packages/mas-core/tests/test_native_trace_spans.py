from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from mas_core.observability.native_spans import build_native_trace_span


def test_native_span_builder_drops_sensitive_attribute_names() -> None:
    span = build_native_trace_span(
        trace_id="trace-1",
        source_kind="tool",
        operation="clock.now",
        service="tool_service",
        attributes={"tool": "clock.now", "token": "drop", "nested": {"x": 1}},
    )
    assert span["attributes"] == {"tool": "clock.now"}


def test_native_span_checker_is_deterministic() -> None:
    script = Path(__file__).resolve().parents[3] / "scripts" / "check_native_trace_spans.py"
    first = subprocess.run(
        [sys.executable, str(script), "--json"],
        check=True,
        capture_output=True,
        text=True,
    )
    second = subprocess.run(
        [sys.executable, str(script), "--json"],
        check=True,
        capture_output=True,
        text=True,
    )
    first_payload = json.loads(first.stdout)
    second_payload = json.loads(second.stdout)
    assert first_payload == second_payload
    assert first_payload["status"] == "pass"
    assert first_payload["licence_metadata_is_gate"] is False

from __future__ import annotations

import asyncio
import importlib.util
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "check_gateway_worker_host_fixture.py"


def _module():
    spec = importlib.util.spec_from_file_location("check_gateway_worker_host_fixture", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_gateway_worker_host_fixture_passes_and_is_payload_free() -> None:
    report = asyncio.run(_module()._run())

    assert report["status"] == "pass"
    assert report["controller_terminal_state"] == "SUCCEEDED"
    assert report["gateway_call_count"] == 1
    assert report["host_admission"]["binding_before"] == "COMMITTED"
    assert report["host_admission"]["binding_after"] == "RELEASED"
    assert report["worker_trace_status"] == "pass"
    assert report["payload_free_report"] is True
    assert report["external_provider_call_performed"] is False
    assert report["sandbox_execution_performed"] is False
    assert report["licence_metadata_is_gate"] is False

from __future__ import annotations

import asyncio
import importlib.util
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "check_gateway_worker_host_failure_fixture.py"


def _module():
    spec = importlib.util.spec_from_file_location(
        "check_gateway_worker_host_failure_fixture", SCRIPT
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_gateway_worker_host_failure_fixture_classifies_and_releases() -> None:
    report = asyncio.run(_module()._run())

    assert report["status"] == "pass"
    assert report["external_provider_call_performed"] is False
    assert report["sandbox_execution_performed"] is False
    assert report["licence_metadata_is_gate"] is False
    assert [case["error_code"] for case in report["cases"]] == [
        "MODEL_GATEWAY_TRANSIENT_FAILURE",
        "MODEL_GATEWAY_REQUEST_REJECTED",
    ]
    assert all(case["binding_after"] == "RELEASED" for case in report["cases"])
    assert all(case["payload_free_report"] is True for case in report["cases"])

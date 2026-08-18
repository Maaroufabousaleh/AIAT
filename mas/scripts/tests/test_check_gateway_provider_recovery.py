from __future__ import annotations

import asyncio
import importlib.util
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "check_gateway_provider_recovery.py"


def _module():
    spec = importlib.util.spec_from_file_location("check_gateway_provider_recovery", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_local_provider_recovery_fixture_proves_fallback_and_cooldown_reset() -> None:
    report = asyncio.run(_module()._run())

    assert report["schema_version"] == "aiat.gateway-provider-recovery.v1"
    assert report["status"] == "pass"
    assert report["attempt_models"] == [
        "fixture-primary/model-v1",
        "fixture-secondary/model-v1",
        "fixture-primary/model-v1",
    ]
    assert report["fallback_used"] is True
    assert report["primary_recovered"] is True
    assert report["primary_cooldown_after_outage"] is True
    assert report["provider_cooldown_after_outage"] is True
    assert report["cooldown_cleared_after_primary_recovery"] is True
    assert report["network_access_performed"] is False
    assert report["external_provider_call_performed"] is False
    assert report["durable_worker_state_changed"] is False
    assert report["licence_metadata_is_gate"] is False

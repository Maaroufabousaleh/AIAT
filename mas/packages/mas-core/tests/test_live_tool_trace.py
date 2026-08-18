"""Contract tests for the host-side live tool-trace endpoint resolution."""

from __future__ import annotations

import importlib.util
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "check_live_tool_trace.py"


def _load_checker():
    spec = importlib.util.spec_from_file_location("check_live_tool_trace_contract", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_local_compose_tool_alias_resolves_to_published_loopback_port() -> None:
    checker = _load_checker()

    resolved, mode = checker._resolve_tool_service_url(
        orchestrator_url="http://127.0.0.1:8000",
        tool_service_url="http://tool-service:8002",
    )

    assert resolved == "http://127.0.0.1:8002"
    assert mode == "local-compose-host-fallback"


def test_remote_tool_service_url_is_never_rewritten() -> None:
    checker = _load_checker()

    resolved, mode = checker._resolve_tool_service_url(
        orchestrator_url="https://orchestrator.example.test",
        tool_service_url="https://tool-service:9443/api",
    )

    assert resolved == "https://tool-service:9443/api"
    assert mode == "configured"

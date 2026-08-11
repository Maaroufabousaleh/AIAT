from __future__ import annotations

import sys
import types
from types import SimpleNamespace

import pytest

from mas_core.protocols.worker_manifest import WorkerManifest
from mas_core.worker_registry.microsoft_agent_framework_adapter import (
    MicrosoftAgentFrameworkAdapter,
)
from mas_core.worker_registry.maf_compatibility import MAFCompatibilityReport


def _manifest(*, instructions: str = "Return a bounded result") -> WorkerManifest:
    return WorkerManifest.model_validate(
        {
            "metadata": {"id": "maf-fixture", "name": "MAF fixture"},
            "runtime_tier": "microsoft_agent_framework",
            "runtime_config": {
                "agent_name": "maf-fixture-agent",
                "instructions": instructions,
            },
            "integration": {"isolation_mode": "microsoft_agent_framework"},
        }
    )


@pytest.mark.anyio
async def test_microsoft_agent_framework_adapter_runs_behind_bounded_boundary(monkeypatch):
    class FakeAgent:
        def __init__(self, *, name: str, instructions: str) -> None:
            self.name = name
            self.instructions = instructions
            self.closed = False

        async def run(self, task):
            return {"agent": self.name, "task": task}

        async def close(self):
            self.closed = True

    monkeypatch.setitem(sys.modules, "agent_framework", types.SimpleNamespace(Agent=FakeAgent))
    monkeypatch.setattr(
        "mas_core.worker_registry.microsoft_agent_framework_adapter.evaluate_microsoft_agent_framework_compatibility",
        lambda: MAFCompatibilityReport(
            status="ready", package_version="1.13.0", mcp_version="1.27.0"
        ),
    )
    adapter = MicrosoftAgentFrameworkAdapter(_manifest())

    await adapter.initialize()
    assert await adapter.health_check() is True
    result = await adapter.send_task(SimpleNamespace(payload={"task": "fixture-task"}))
    assert result["status"] == "completed"
    assert result["runtime"] == "microsoft_agent_framework"
    assert result["output"] == {"agent": "maf-fixture-agent", "task": "fixture-task"}

    await adapter.shutdown()
    assert await adapter.health_check() is False


@pytest.mark.anyio
async def test_microsoft_agent_framework_adapter_fails_closed_without_instructions(monkeypatch):
    class FakeAgent:
        def __init__(self, **_kwargs):
            raise AssertionError("agent construction must not happen without instructions")

    monkeypatch.setitem(sys.modules, "agent_framework", types.SimpleNamespace(Agent=FakeAgent))
    monkeypatch.setattr(
        "mas_core.worker_registry.microsoft_agent_framework_adapter.evaluate_microsoft_agent_framework_compatibility",
        lambda: MAFCompatibilityReport(
            status="ready", package_version="1.13.0", mcp_version="1.27.0"
        ),
    )
    adapter = MicrosoftAgentFrameworkAdapter(_manifest(instructions=""))

    await adapter.initialize()
    result = await adapter.send_task(SimpleNamespace(payload={"input": "fixture-task"}))
    assert result["status"] == "unavailable"
    assert "instructions" in result["reason"]


@pytest.mark.anyio
async def test_microsoft_agent_framework_adapter_reports_missing_package(monkeypatch):
    real_import = __import__("importlib").import_module

    def missing(name: str):
        if name == "agent_framework":
            raise ImportError("fixture package missing")
        return real_import(name)

    monkeypatch.setattr(
        "mas_core.worker_registry.microsoft_agent_framework_adapter.importlib.import_module",
        missing,
    )
    adapter = MicrosoftAgentFrameworkAdapter(_manifest())

    await adapter.initialize()
    result = await adapter.send_task(SimpleNamespace(payload={"task": "fixture-task"}))
    assert result["status"] == "unavailable"
    assert "not installed" in result["reason"]

"""Tests for the deterministic default-runtime adapter conformance probe."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

MAS_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = MAS_ROOT / "scripts" / "check_runtime_adapter_conformance.py"


def _manifest(runtime_tier: str):
    from mas_core.protocols.worker_manifest import WorkerManifest

    return WorkerManifest.model_validate(
        {
            "metadata": {"id": f"{runtime_tier}-translation", "name": runtime_tier},
            "runtime_tier": runtime_tier,
            "integration": {"isolation_mode": runtime_tier},
        }
    )


def test_runtime_adapter_fixture_conformance_exercises_both_default_adapters() -> None:
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--json"],
        cwd=MAS_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["schema_version"] == "aiat.runtime-adapter-conformance.v1"
    assert report["mode"] == "fixture"
    assert report["status"] == "pass"
    assert report["certification_boundary"]["framework_execution"] == "fixture_only"
    assert {row["runtime_id"] for row in report["runtimes"]} == {"langgraph", "crewai"}
    assert all(row["status"] == "pass" for row in report["runtimes"])
    assert all(row["external_model_call"] is False for row in report["runtimes"])


def test_runtime_adapter_live_mode_is_blocked_when_packages_are_absent() -> None:
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--live", "--json"],
        cwd=MAS_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    report = json.loads(result.stdout)
    # The host development venv intentionally does not install the optional
    # framework packages; the Compose image is the environment for live import
    # evidence. If a developer has installed both packages locally, live mode
    # should pass instead of being treated as a failure.
    if report["status"] == "blocked":
        assert result.returncode == 2
        assert any(row["status"] == "blocked" for row in report["runtimes"])
    else:
        assert result.returncode == 0
        assert report["status"] == "pass"


def test_framework_adapters_preserve_project_and_message_context() -> None:
    from mas_core.worker_registry.crewai_adapter import CrewAIAdapter
    from mas_core.worker_registry.langgraph_adapter import LangGraphAdapter

    envelope = SimpleNamespace(
        project_id="project-translation",
        payload={"task": "task", "context": "context", "messages": [{"role": "user"}]},
    )
    for adapter in (
        LangGraphAdapter(_manifest("langgraph")),
        CrewAIAdapter(_manifest("crewai")),
    ):
        translated = adapter._translate_input(envelope)
        assert translated == {
            "task": "task",
            "context": "context",
            "project_id": "project-translation",
            "messages": [{"role": "user"}],
        }


def test_framework_adapters_normalize_missing_project_id() -> None:
    from mas_core.worker_registry.crewai_adapter import CrewAIAdapter
    from mas_core.worker_registry.langgraph_adapter import LangGraphAdapter

    envelope = SimpleNamespace(project_id=None, payload={})
    for adapter in (
        LangGraphAdapter(_manifest("langgraph")),
        CrewAIAdapter(_manifest("crewai")),
    ):
        assert adapter._translate_input(envelope)["project_id"] is None

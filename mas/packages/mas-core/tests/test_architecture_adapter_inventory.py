"""Read-only characterization of the legacy runtime-adapter inventory."""

from __future__ import annotations

import ast
import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[4]
MAS_ROOT = REPO_ROOT / "mas"
INVENTORY_SCRIPT = MAS_ROOT / "scripts" / "check_runtime_adapter_inventory.py"
LEGACY_MODULES = (
    "langgraph_adapter",
    "crewai_adapter",
    "autogen_adapter",
    "letta_adapter",
    "microsoft_agent_framework_adapter",
)


def _production_python_files() -> list[Path]:
    # Keep the read-only inventory bounded to checked-in service source trees.
    # A broad ``apps/**`` walk also traverses operator-created temporary
    # directories that may be mounted or permission-protected.
    roots = [
        MAS_ROOT / "apps/orchestrator-api/orchestrator_api",
        MAS_ROOT / "apps/team-runner/team_runner",
        MAS_ROOT / "apps/message-router/message_router",
        MAS_ROOT / "apps/tool-service/tool_service",
        MAS_ROOT / "apps/identity-service/identity_service",
        MAS_ROOT / "apps/pm-gateway/pm_gateway",
        MAS_ROOT / "packages/mas-core/mas_core",
    ]
    files: list[Path] = []
    for root in roots:
        for path in root.rglob("*.py"):
            if "tests" not in path.parts and "__pycache__" not in path.parts:
                files.append(path)
    return files


def test_legacy_factory_has_no_indexed_service_caller() -> None:
    factory = MAS_ROOT / "packages/mas-core/mas_core/worker_registry/adapter_factory.py"
    callers: list[Path] = []

    for path in _production_python_files():
        if path == factory:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        if any(
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "create_adapter"
            for node in ast.walk(tree)
        ):
            callers.append(path)

    assert callers == []


def test_legacy_factory_retains_dynamic_reachability_for_each_standalone_adapter() -> None:
    factory_source = (
        MAS_ROOT / "packages/mas-core/mas_core/worker_registry/adapter_factory.py"
    ).read_text(encoding="utf-8")

    for module in LEGACY_MODULES:
        assert module in factory_source


def test_canonical_runtime_adapter_family_has_active_service_references() -> None:
    production_sources = [path.read_text(encoding="utf-8") for path in _production_python_files()]
    assert any("worker_registry.runtime_adapters" in source for source in production_sources)
    assert "class LangGraphAdapter" in (
        MAS_ROOT / "packages/mas-core/mas_core/worker_registry/runtime_adapters.py"
    ).read_text(encoding="utf-8")
    assert "class CrewAIAdapter" in (
        MAS_ROOT / "packages/mas-core/mas_core/worker_registry/runtime_adapters.py"
    ).read_text(encoding="utf-8")


def test_nonproduction_legacy_references_are_visible_before_later_consolidation() -> None:
    conformance_script = (
        MAS_ROOT / "scripts/check_runtime_adapter_conformance.py"
    ).read_text(encoding="utf-8")
    assert "worker_registry.runtime_adapters" in conformance_script
    for module in ("langgraph_adapter.py", "crewai_adapter.py"):
        source = (MAS_ROOT / "packages/mas-core/mas_core/worker_registry" / module).read_text(
            encoding="utf-8"
        )
        assert "from .runtime_adapters import" in source


def test_framework_compatibility_modules_reexport_canonical_classes() -> None:
    from mas_core.worker_registry.crewai_adapter import (
        CrewAIAdapter as CompatibilityCrewAIAdapter,
    )
    from mas_core.worker_registry.crewai_adapter import (
        CrewAICapabilities as CompatibilityCrewAICapabilities,
    )
    from mas_core.worker_registry.langgraph_adapter import (
        LangGraphAdapter as CompatibilityLangGraphAdapter,
    )
    from mas_core.worker_registry.langgraph_adapter import (
        LangGraphCapabilities as CompatibilityLangGraphCapabilities,
    )
    from mas_core.worker_registry.runtime_adapters import (
        CrewAIAdapter,
        CrewAICapabilities,
        LangGraphAdapter,
        LangGraphCapabilities,
    )

    assert CompatibilityLangGraphAdapter is LangGraphAdapter
    assert CompatibilityLangGraphCapabilities is LangGraphCapabilities
    assert CompatibilityCrewAIAdapter is CrewAIAdapter
    assert CompatibilityCrewAICapabilities is CrewAICapabilities

    optional_memory_yaml = (
        MAS_ROOT / "docs/provenance/optional_memory_services.yaml"
    ).read_text(encoding="utf-8")
    optional_memory_json = (
        MAS_ROOT / "docs/provenance/optional_memory_services_contract.json"
    ).read_text(encoding="utf-8")
    assert "mas_core.worker_registry.letta_adapter.LettaAdapter" in optional_memory_yaml
    assert "mas_core.worker_registry.letta_adapter.LettaAdapter" in optional_memory_json

    maf_script = (MAS_ROOT / "scripts/check_maf_runtime.py").read_text(encoding="utf-8")
    assert "worker_registry.microsoft_agent_framework_adapter" in maf_script


def test_machine_readable_inventory_preserves_current_legacy_reachability() -> None:
    result = subprocess.run(
        [sys.executable, str(INVENTORY_SCRIPT), "--json"],
        cwd=MAS_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["schema_version"] == "aiat.runtime-adapter-inventory.v1"
    modules = {row["path"]: row for row in report["modules"]}

    factory = modules["mas/packages/mas-core/mas_core/worker_registry/adapter_factory.py"]
    assert factory["exists"] is True
    # The package initializer has only a descriptive module-list entry; the
    # existing AST characterization above is the assertion for no production
    # ``create_adapter()`` caller.
    assert factory["production_reference_paths"] == [
        "mas/packages/mas-core/mas_core/worker_registry/__init__.py"
    ]
    assert "mas/packages/mas-core/tests/test_external_worker_adapter.py" in factory["test_reference_paths"]

    langgraph = modules["mas/packages/mas-core/mas_core/worker_registry/langgraph_adapter.py"]
    crewai = modules["mas/packages/mas-core/mas_core/worker_registry/crewai_adapter.py"]
    assert "mas/scripts/check_runtime_adapter_conformance.py" not in langgraph["dynamic_reference_paths"]
    assert "mas/scripts/check_runtime_adapter_conformance.py" not in crewai["dynamic_reference_paths"]

    letta = modules["mas/packages/mas-core/mas_core/worker_registry/letta_adapter.py"]
    assert "mas/docs/provenance/optional_memory_services.yaml" in letta["manifest_config_paths"]
    assert "mas/docs/provenance/optional_memory_services_contract.json" in letta["manifest_config_paths"]

    canonical = modules["mas/packages/mas-core/mas_core/worker_registry/runtime_adapters.py"]
    assert canonical["family"] == "canonical"
    assert canonical["production_reference_paths"]

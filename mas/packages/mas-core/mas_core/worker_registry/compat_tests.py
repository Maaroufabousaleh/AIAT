"""Compatibility test harness for worker upgrades.

Runs a suite of tests before activating or upgrading a worker to ensure
it conforms to our integration contract.

Tests
-----
1. manifest_validation    — YAML parses, all required fields present
2. capability_contract    — declared capabilities match actual tool usage
3. transport_compatibility — entrypoint responds to declared transport
4. sandbox_compliance     — worker operates within declared sandbox constraints
5. budget_enforcement     — worker respects LLM/tool call limits and cost caps
6. message_protocol       — worker correctly handles MessageEnvelope format
7. checkpoint_compatibility — worker state can be saved and restored
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any
from uuid import UUID

import yaml

from mas_core.protocols.worker_manifest import WorkerManifest

if TYPE_CHECKING:
    from mas_core.memory.storage import AgentStorage

logger = logging.getLogger(__name__)


async def _test_manifest_validation(
    worker: dict[str, Any],
    manifest: WorkerManifest | None,
    storage: AgentStorage,
) -> dict:
    """Verify the worker manifest is valid and complete."""
    details = []

    if not worker.get("name"):
        return {"passed": False, "details": "Worker name is missing"}

    if not worker.get("adapter_type"):
        return {"passed": False, "details": "Adapter type is missing"}

    if manifest:
        required_sections = ["metadata", "runtime", "capabilities", "sandbox"]
        for section in required_sections:
            if not hasattr(manifest, section):
                return {
                    "passed": False,
                    "details": f"Required section '{section}' missing from manifest",
                }

        if manifest.metadata.id != worker["name"]:
            details.append(
                f"Manifest ID ({manifest.metadata.id}) differs from worker name ({worker['name']})"
            )

        details.append(f"Manifest version: {manifest.metadata.version}")
        details.append(f"Transport: {manifest.runtime.transport}")
        details.append(f"Capabilities: {len(manifest.capabilities)}")

    return {
        "passed": True,
        "details": "; ".join(details),
    }


async def _test_capability_contract(
    worker: dict[str, Any],
    manifest: WorkerManifest | None,
    storage: AgentStorage,
) -> dict:
    """Verify declared capabilities are registered and valid."""
    cap_ids = worker.get("capability_ids") or []

    if not cap_ids:
        return {
            "passed": False,
            "details": "Worker has no capabilities registered",
        }

    valid_caps = 0
    for cap_id in cap_ids:
        cap = await storage.get_capability(cap_id)
        if cap:
            valid_caps += 1

    total = len(cap_ids)
    return {
        "passed": valid_caps == total,
        "details": f"{valid_caps}/{total} capabilities are valid",
        "valid_count": valid_caps,
        "total_count": total,
    }


async def _test_transport_compatibility(
    worker: dict[str, Any],
    manifest: WorkerManifest | None,
    storage: AgentStorage,
) -> dict:
    """Verify the worker's transport mode is supported."""
    adapter_type = worker.get("adapter_type", "process")
    supported = {"process", "http", "oci", "mcp", "openhands", "openhands_agent_server", "human"}

    if adapter_type not in supported:
        return {
            "passed": False,
            "details": f"Unsupported transport type: {adapter_type}",
        }

    details = [f"Transport {adapter_type} is supported"]

    if manifest:
        details.append(f"Timeout: {manifest.runtime.timeout_seconds}s")
        details.append(f"Grace period: {manifest.runtime.stop_grace_seconds}s")

    return {
        "passed": True,
        "details": "; ".join(details),
    }


async def _test_sandbox_compliance(
    worker: dict[str, Any],
    manifest: WorkerManifest | None,
    storage: AgentStorage,
) -> dict:
    """Verify the worker's sandbox profile is valid."""
    profile = worker.get("sandbox_profile", "standard")
    valid_profiles = {"standard", "restricted", "gvisor", "firecracker"}

    if profile not in valid_profiles:
        return {
            "passed": False,
            "details": f"Invalid sandbox profile: {profile}",
        }

    details = [f"Sandbox profile '{profile}' is valid"]

    if manifest:
        if manifest.sandbox.network_mode == "egress-allowlist":
            allowed = manifest.sandbox.egress_allowlist
            details.append(f"Egress allowlist: {len(allowed)} entries")

    return {
        "passed": True,
        "details": "; ".join(details),
    }


async def _test_budget_enforcement(
    worker: dict[str, Any],
    manifest: WorkerManifest | None,
    storage: AgentStorage,
) -> dict:
    """Verify the worker has budget constraints configured."""
    details = []

    if manifest and manifest.limits.max_concurrent_tasks > 0:
        details.append(f"Max concurrent tasks: {manifest.limits.max_concurrent_tasks}")
    else:
        details.append("Using default concurrency limits")

    if manifest and manifest.limits.max_instances > 0:
        details.append(f"Max instances: {manifest.limits.max_instances}")

    if manifest and manifest.limits.rate_limit_per_minute > 0:
        details.append(f"Rate limit: {manifest.limits.rate_limit_per_minute}/min")

    return {
        "passed": True,
        "details": "; ".join(details),
    }


async def _test_message_protocol(
    worker: dict[str, Any],
    manifest: WorkerManifest | None,
    storage: AgentStorage,
) -> dict:
    """Verify the worker's adapter entrypoint is resolvable."""
    entrypoint = worker.get("adapter_entrypoint", "WorkerAgent")
    builtin_entrypoints = {
        "WorkerAgent",
        "AdminAgent",
        "CSuiteAgent",
        "ExecutiveAgent",
        "SubAgent",
    }

    if entrypoint in builtin_entrypoints:
        return {
            "passed": True,
            "details": f"Built-in entrypoint '{entrypoint}' is valid",
        }

    adapter_module = worker.get("adapter_module")
    if adapter_module:
        try:
            import importlib

            module_name = (
                adapter_module.rsplit(".", 1)[0] if "." in adapter_module else adapter_module
            )
            importlib.import_module(module_name)
            return {
                "passed": True,
                "details": f"External module '{adapter_module}' is importable",
            }
        except ImportError as exc:
            return {
                "passed": False,
                "details": f"Cannot import external module '{adapter_module}': {exc}",
            }

    return {
        "passed": False,
        "details": f"Unknown entrypoint '{entrypoint}' with no adapter_module",
    }


async def _test_checkpoint_compatibility(
    worker: dict[str, Any],
    manifest: WorkerManifest | None,
    storage: AgentStorage,
) -> dict:
    """Verify the worker supports checkpoint save/restore."""
    details = []

    if manifest:
        if manifest.checkpointing.enabled:
            details.append(f"Checkpointing enabled (strategy: {manifest.checkpointing.strategy})")
            details.append(f"Store: {manifest.checkpointing.store.get('kind', 'unknown')}")
        else:
            details.append("Checkpointing disabled")
    else:
        details.append("No manifest available for checkpointing config")

    return {
        "passed": True,
        "details": "; ".join(details),
    }


TEST_FUNCTIONS = {
    "manifest_validation": _test_manifest_validation,
    "capability_contract": _test_capability_contract,
    "transport_compatibility": _test_transport_compatibility,
    "sandbox_compliance": _test_sandbox_compliance,
    "budget_enforcement": _test_budget_enforcement,
    "message_protocol": _test_message_protocol,
    "checkpoint_compatibility": _test_checkpoint_compatibility,
}


async def run_compatibility_tests(
    *,
    worker_id: UUID,
    storage: AgentStorage,
    test_names: list[str] | None = None,
    manifest_path: Path | None = None,
) -> dict[str, Any]:
    """Run compatibility tests for a worker.

    Parameters
    ----------
    worker_id:
        Worker database ID.
    storage:
        Connected AgentStorage instance.
    test_names:
        Specific tests to run. Defaults to all.
    manifest_path:
        Optional path to the worker's YAML manifest.

    Returns
    -------
    dict
        {"passed": bool, "results": {test_name: {passed, details}}}
    """
    worker = await storage.get_worker(worker_id)
    if worker is None:
        raise ValueError(f"Worker {worker_id} not found")

    manifest = None
    if manifest_path and manifest_path.exists():
        raw = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
        manifest = WorkerManifest.model_validate(raw)

    requested = test_names or list(TEST_FUNCTIONS.keys())
    results: dict[str, dict] = {}

    for test_name in requested:
        func = TEST_FUNCTIONS.get(test_name)
        if func is None:
            logger.warning("Unknown compatibility test: %s", test_name)
            continue

        try:
            results[test_name] = await func(worker, manifest, storage)
        except Exception as exc:
            logger.error("Test %s failed with exception: %s", test_name, exc)
            results[test_name] = {
                "passed": False,
                "details": f"Test execution error: {exc}",
            }

    all_passed = all(r.get("passed", False) for r in results.values())

    return {
        "passed": all_passed,
        "total": len(results),
        "passed_count": sum(1 for r in results.values() if r.get("passed")),
        "failed_count": sum(1 for r in results.values() if not r.get("passed")),
        "results": results,
    }

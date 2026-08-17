"""Certify the isolated Microsoft Agent Framework adapter profile.

The selected interpreter is probed in a subprocess so the default AIAT
workspace and production images remain unchanged. The probe uses MAF's real
``Agent`` implementation with an in-process fake chat client; it never calls a
provider, MCP server, project, tool, or credential store. A passing result is
runtime/adapter evidence only. Security, sandbox, approval, canary, live-run,
and rollback gates remain separate.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

MAS_ROOT = Path(__file__).resolve().parents[1]
PROFILE_ROOT = MAS_ROOT / "infra" / "runtime" / "maf"
REQUIREMENTS_PATH = PROFILE_ROOT / "requirements.txt"
CHECK_SCHEMA = "aiat.maf-runtime-certification.v1"
EXPECTED_MAF_VERSION = "1.13.0"
EXPECTED_MCP_VERSION = "1.29.0"
EXPECTED_MCP_SPECIFIER = ">=1.27,<2"
_REQUIREMENT_RE = re.compile(r"^([A-Za-z0-9][A-Za-z0-9_.-]*)\s*([<>=!~].*)?$")


def _requirements_contract() -> tuple[dict[str, str], list[str]]:
    errors: list[str] = []
    try:
        lines = REQUIREMENTS_PATH.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        return {}, [f"cannot read optional MAF requirements: {type(exc).__name__}"]
    declared: dict[str, str] = {}
    for raw in lines:
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        match = _REQUIREMENT_RE.fullmatch(line)
        if match is None:
            errors.append("optional MAF requirements contain an invalid declaration")
            continue
        name = match.group(1).lower().replace("_", "-")
        specifier = match.group(2) or ""
        declared[name] = specifier
    expected = {
        "agent-framework": f"=={EXPECTED_MAF_VERSION}",
        "mcp": f"=={EXPECTED_MCP_VERSION}",
    }
    for name, specifier in expected.items():
        if declared.get(name) != specifier:
            errors.append(f"optional profile must declare {name}{specifier}")
    return declared, errors


_RUNTIME_PROBE = r'''
import asyncio
import importlib.metadata
import json
import sys
from types import SimpleNamespace


def emit(payload, code=0):
    print(json.dumps(payload, sort_keys=True, separators=(",", ":")))
    raise SystemExit(code)


try:
    import agent_framework
    import mcp
    from agent_framework import Agent, ChatResponse, Message
    sys.path.insert(0, sys.argv[1])
    from mas_core.protocols.worker_manifest import WorkerManifest
    from mas_core.worker_registry.microsoft_agent_framework_adapter import (
        MicrosoftAgentFrameworkAdapter,
    )
except Exception as exc:
    emit({
        "status": "blocked",
        "reason": f"optional runtime import failed: {type(exc).__name__}",
        "package_import_available": False,
        "provider_network_calls": False,
        "mutation_performed": False,
    }, 3)


class FakeClient:
    additional_properties = {}

    def __init__(self):
        self.calls = 0

    async def get_response(self, messages, **kwargs):
        del messages, kwargs
        self.calls += 1
        return ChatResponse(
            messages=[Message("assistant", ["bounded-fixture-result"])],
            response_id="aiat-maf-fixture",
        )


async def run_probe():
    client = FakeClient()
    manifest = WorkerManifest.model_validate({
        "metadata": {"id": "maf-isolated-fixture", "name": "MAF isolated fixture"},
        "runtime_tier": "microsoft_agent_framework",
        "runtime_config": {
            "agent_name": "aiat-maf-fixture",
            "instructions": "Return the bounded fixture result.",
        },
        "integration": {"isolation_mode": "microsoft_agent_framework"},
    })
    adapter = MicrosoftAgentFrameworkAdapter(manifest, client=client)
    await adapter.initialize()
    result = await adapter.send_task(SimpleNamespace(payload={"task": "fixture-task"}))
    healthy_before_shutdown = await adapter.health_check()
    await adapter.shutdown()
    healthy_after_shutdown = await adapter.health_check()
    output = result.get("output") if isinstance(result, dict) else None
    return {
        "status": "pass" if (
            result.get("status") == "completed"
            and output == "bounded-fixture-result"
            and healthy_before_shutdown
            and not healthy_after_shutdown
            and client.calls == 1
        ) else "fail",
        "result_status": result.get("status"),
        "output": output if isinstance(output, (str, int, float, bool)) or output is None else str(output),
        "fake_client_calls": client.calls,
        "health_before_shutdown": healthy_before_shutdown,
        "health_after_shutdown": healthy_after_shutdown,
        "adapter_class": type(adapter).__name__,
        "agent_symbol": "Agent" if getattr(agent_framework, "Agent", None) is Agent else "other",
        "provider_network_calls": False,
        "mutation_performed": False,
    }


try:
    payload = asyncio.run(run_probe())
    payload.update({
        "package_import_available": True,
        "agent_framework_version": importlib.metadata.version("agent-framework"),
        "mcp_version": importlib.metadata.version("mcp"),
    })
    emit(payload, 0 if payload["status"] == "pass" else 1)
except Exception as exc:
    emit({
        "status": "fail",
        "reason": f"deterministic adapter probe failed: {type(exc).__name__}",
        "provider_network_calls": False,
        "mutation_performed": False,
    }, 1)
'''


def _probe(interpreter: Path) -> dict[str, Any]:
    env = os.environ.copy()
    mas_core = str(MAS_ROOT / "packages" / "mas-core")
    existing = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = mas_core if not existing else mas_core + os.pathsep + existing
    try:
        result = subprocess.run(
            [str(interpreter), "-c", _RUNTIME_PROBE, mas_core],
            cwd=MAS_ROOT,
            env=env,
            capture_output=True,
            text=True,
            check=False,
            timeout=90.0,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {
            "status": "blocked",
            "reason": f"optional runtime interpreter unavailable: {type(exc).__name__}",
            "provider_network_calls": False,
            "mutation_performed": False,
        }
    try:
        payload = json.loads(result.stdout.strip())
    except json.JSONDecodeError:
        return {
            "status": "blocked",
            "reason": "optional runtime probe returned invalid JSON",
            "provider_network_calls": False,
            "mutation_performed": False,
        }
    if not isinstance(payload, dict):
        return {
            "status": "blocked",
            "reason": "optional runtime probe returned a non-object",
            "provider_network_calls": False,
            "mutation_performed": False,
        }
    return payload


def build_report(*, interpreter: Path) -> dict[str, Any]:
    declared, contract_errors = _requirements_contract()
    probe = _probe(interpreter) if not contract_errors else {
        "status": "blocked",
        "reason": "optional profile contract is invalid",
        "provider_network_calls": False,
        "mutation_performed": False,
    }
    errors = list(contract_errors)
    if probe.get("status") == "fail":
        errors.append(str(probe.get("reason") or "optional runtime adapter probe failed"))
    status = "fail" if errors else ("pass" if probe.get("status") == "pass" else "blocked")
    return {
        "schema_version": CHECK_SCHEMA,
        "status": status,
        "mode": "isolated-runtime-python",
        "interpreter": interpreter.name,
        "profile": {
            "requirements_file": "infra/runtime/maf/requirements.txt",
            "declared_versions": declared,
            "agent_framework_version": EXPECTED_MAF_VERSION,
            "mcp_version": EXPECTED_MCP_VERSION,
            "mcp_compatibility": EXPECTED_MCP_SPECIFIER,
        },
        "probe": probe,
        "errors": errors,
        "scope": "isolated MAF import, AIAT adapter construction, fake-client bounded task, response normalization, health, and shutdown",
        "certification_boundary": {
            "package_imports": "checked",
            "adapter_execution": "deterministic_fake_client",
            "provider_network_calls": "not_performed",
            "mcp_server_calls": "not_performed",
            "project_or_tool_calls": "not_performed",
            "security_scan": "not_checked",
            "sandbox": "not_checked",
            "model_backed_canary": "not_checked",
            "live_worker_run": "not_checked",
            "rollback": "not_checked",
        },
        "mutation_performed": False,
        "network_access_performed": False,
        "licence_metadata_is_gate": False,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--python",
        dest="interpreter",
        type=Path,
        default=Path(sys.executable),
        help="isolated runtime interpreter to probe (default: current Python)",
    )
    parser.add_argument("--json", action="store_true", help="emit JSON")
    args = parser.parse_args(argv)
    report = build_report(interpreter=args.interpreter)
    if args.json:
        print(json.dumps(report, sort_keys=True, indent=2))
    else:
        print(f"MAF runtime certification: {report['status']}")
        if report["errors"]:
            for error in report["errors"]:
                print(f"MAF runtime certification: {error}", file=sys.stderr)
    return 2 if report["status"] == "blocked" else (1 if report["status"] == "fail" else 0)


if __name__ == "__main__":
    raise SystemExit(main())

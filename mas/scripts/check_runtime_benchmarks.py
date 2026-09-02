"""Run dependency-backed runtime benchmarks through the orchestrator API.

This command is a read-only readiness probe for the existing ``/runtimes`` and
``/runtimes/benchmark`` endpoints. It does not create a worker, dispatch a
project task, access external tools, or grant credentials. Missing URL/auth,
an unavailable API, missing runtime packages, validation failure, or malformed
responses are ``blocked`` and exit 2. A completed dependency dry-run proves
only runtime benchmark readiness; sandbox, canary, live worker-run, rollback,
and provider evidence remain separate.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any

import httpx

BENCHMARK_SCHEMA = "aiat.runtime-benchmark-readiness.v1"
DEFAULT_RUNTIME_IDS = ("langgraph", "crewai")


def default_runtime_config(runtime_id: str) -> dict[str, Any]:
    """Return a deterministic, side-effect-free validation config.

    The orchestrator benchmark endpoint validates a runtime before it performs
    its dependency-backed dry-run.  Sending ``{}`` therefore proves only that
    validation rejects an underspecified config; it cannot exercise an
    installed runtime.  These configs intentionally use in-memory/read-only
    settings and contain no provider, tool, credential, or project state.
    """

    configs: dict[str, dict[str, Any]] = {
        "langgraph": {
            "state_schema": {"messages": []},
            "checkpointer": "memory",
            "interrupt_before": [],
            "interrupt_after": [],
        },
        "crewai": {
            "crew_config": {
                "agents": [{"role": "runtime-smoke-agent"}],
                "tasks": [{"description": "bounded runtime smoke task"}],
            },
            "process": "sequential",
        },
        "microsoft_agent_framework": {
            "instructions": "Return a bounded runtime smoke acknowledgement.",
            "agent_name": "aiat-runtime-smoke",
            "sandbox_profile": "gvisor",
        },
        "autogen": {
            "termination_strategy": {"type": "max_messages"},
            "max_round": 1,
        },
        "letta": {
            "persona": "A bounded runtime smoke worker.",
            "embedding_model": "fixture-embedding",
            "persistence_store": "memory",
            "memory_block_types": ["persona"],
        },
    }
    return dict(configs.get(runtime_id, {}))


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="query a running orchestrator API")
    parser.add_argument("--json", action="store_true", help="emit JSON")
    parser.add_argument(
        "--url",
        default=os.getenv("AIAT_ORCHESTRATOR_URL", os.getenv("ORCHESTRATOR_API_URL", "")),
        help="orchestrator base URL (or AIAT_ORCHESTRATOR_URL/ORCHESTRATOR_API_URL)",
    )
    parser.add_argument(
        "--api-key",
        default=os.getenv("AIAT_OPERATOR_API_KEY", os.getenv("AIAT_API_KEY", os.getenv("MAS_API_KEY", ""))),
        help="optional operator bearer key (AIAT_OPERATOR_API_KEY/AIAT_API_KEY/MAS_API_KEY); never included in the report",
    )
    parser.add_argument("--timeout", type=float, default=15.0)
    parser.add_argument(
        "--runtime",
        dest="runtimes",
        action="append",
        choices=("langgraph", "crewai", "microsoft_agent_framework", "autogen", "letta"),
        help="runtime ID to benchmark; repeat to select more than one (default: LangGraph/CrewAI)",
    )
    return parser


def _blocked(reason: str, *, url_configured: bool = False) -> dict[str, Any]:
    return {
        "schema_version": BENCHMARK_SCHEMA,
        "mode": "live",
        "status": "blocked",
        "reason": reason,
        "url_configured": url_configured,
        "runtimes": [],
        "scope": "orchestrator dependency-backed dry-run only",
        "certification_boundary": {
            "package_benchmark": "not_checked",
            "sandbox": "not_checked",
            "canary": "not_checked",
            "live_worker_run": "not_checked",
            "rollback": "not_checked",
        },
    }


def _static_report() -> dict[str, Any]:
    return {
        "schema_version": BENCHMARK_SCHEMA,
        "mode": "static",
        "status": "pass",
        "reason": "live mode was not requested",
        "runtimes": list(DEFAULT_RUNTIME_IDS),
        "scope": "declaration only; live benchmark not checked",
        "certification_boundary": {
            "package_benchmark": "not_checked",
            "sandbox": "not_checked",
            "canary": "not_checked",
            "live_worker_run": "not_checked",
            "rollback": "not_checked",
        },
    }


def inspect_live(*, url: str, api_key: str, runtime_ids: tuple[str, ...], timeout: float) -> dict[str, Any]:
    if not url.strip():
        return _blocked("missing live configuration: orchestrator URL")
    if not runtime_ids:
        return _blocked("no runtime IDs were selected", url_configured=True)
    endpoint = url.rstrip("/")
    headers = {"Authorization": f"Bearer {api_key}"} if api_key.strip() else {}
    try:
        catalogue_response = httpx.get(f"{endpoint}/runtimes", headers=headers, timeout=timeout)
        catalogue_response.raise_for_status()
        catalogue = catalogue_response.json()
    except (httpx.HTTPError, ValueError) as exc:
        return _blocked(f"runtime catalogue unavailable: {type(exc).__name__}", url_configured=True)
    if not isinstance(catalogue, dict) or not isinstance(catalogue.get("runtimes"), list):
        return _blocked("orchestrator returned an invalid runtime catalogue", url_configured=True)
    available = {
        str(row.get("id")): row
        for row in catalogue["runtimes"]
        if isinstance(row, dict) and str(row.get("id", "")).strip()
    }
    rows: list[dict[str, Any]] = []
    blocked: list[str] = []
    failed: list[str] = []
    for runtime_id in runtime_ids:
        declaration = available.get(runtime_id)
        if declaration is None:
            rows.append({"runtime_id": runtime_id, "status": "blocked", "reason": "runtime is absent from API catalogue"})
            blocked.append(runtime_id)
            continue
        try:
            response = httpx.post(
                f"{endpoint}/runtimes/benchmark",
                headers=headers,
                json={
                    "runtime_tier": runtime_id,
                    "runtime_config": default_runtime_config(runtime_id),
                    "dry_run": True,
                },
                timeout=timeout,
            )
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            rows.append({"runtime_id": runtime_id, "status": "blocked", "reason": f"benchmark unavailable: {type(exc).__name__}"})
            blocked.append(runtime_id)
            continue
        if not isinstance(payload, dict):
            rows.append({"runtime_id": runtime_id, "status": "blocked", "reason": "benchmark response was not an object"})
            blocked.append(runtime_id)
            continue
        benchmark_status = str(payload.get("status", ""))
        result: dict[str, Any] = {
            "runtime_id": runtime_id,
            "catalogue_status": str(declaration.get("status", "unknown")),
            "sandbox_required": (declaration.get("policy") or {}).get("sandbox_required"),
            "status": benchmark_status or "blocked",
        }
        benchmark_results = payload.get("benchmark_results")
        if isinstance(benchmark_results, dict):
            result["benchmark"] = {
                key: benchmark_results.get(key)
                for key in ("elapsed_ms", "tasks_run", "tasks_passed", "timeout_seconds")
                if key in benchmark_results
            }
        missing_packages = payload.get("missing_packages")
        if isinstance(missing_packages, list):
            result["missing_packages"] = [str(value) for value in missing_packages]
        rows.append(result)
        if benchmark_status == "dry_run_completed":
            continue
        if benchmark_status in {
            "package_unavailable",
            "benchmark_timeout",
            "benchmark_error",
            "skipped",
            "",
        }:
            blocked.append(runtime_id)
        else:
            failed.append(runtime_id)
    status = "fail" if failed else ("blocked" if blocked else "pass")
    reason = None
    if failed:
        reason = f"runtime benchmark failed: {', '.join(failed)}"
    elif blocked:
        reason = f"runtime benchmark evidence unavailable: {', '.join(blocked)}"
    return {
        "schema_version": BENCHMARK_SCHEMA,
        "mode": "live",
        "status": status,
        "reason": reason,
        "url_configured": True,
        "runtimes": rows,
        "scope": "orchestrator dependency-backed dry-run only",
        "certification_boundary": {
            "package_benchmark": "checked",
            "sandbox": "not_checked",
            "canary": "not_checked",
            "live_worker_run": "not_checked",
            "rollback": "not_checked",
        },
    }


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if not args.live:
        report = _static_report()
    else:
        report = inspect_live(
            url=args.url,
            api_key=args.api_key,
            runtime_ids=tuple(args.runtimes or DEFAULT_RUNTIME_IDS),
            timeout=args.timeout,
        )
    if args.json:
        print(json.dumps(report, sort_keys=True, indent=2))
    else:
        print(f"runtime benchmarks: {report['status']} — {report.get('reason', 'no reason')}")
    return 2 if report["status"] == "blocked" else (1 if report["status"] == "fail" else 0)


if __name__ == "__main__":
    sys.exit(main())

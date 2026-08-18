"""Run a bounded local worker-host loss/recovery soak.

Each iteration starts the production host-loss/requeue/reassignment checker in
its own Python child process.  The child exercises AIAT's durable host lease,
Worker Run claim, stale-executor rejection, alternate-host reassignment, retry,
Postgres reopen, and scoped cleanup path.  The parent retains only scalar
iteration summaries and never forwards child payloads or diagnostic text.

This is repeated same-Compose-host recovery evidence.  It does not claim
independent deployed hosts, split-brain protection across machines, gVisor,
Firecracker, provider outage recovery, or disaster recovery.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

CHECK_SCHEMA = "aiat.worker-host-loss-queue-recovery-soak-postgres-certification.v1"
DEFAULT_ITERATIONS = 3
MAX_ITERATIONS = 10
DEFAULT_TIMEOUT_SECONDS = 120
MIN_TIMEOUT_SECONDS = 10
MAX_TIMEOUT_SECONDS = 300
PAYLOAD_MARKER = "aiat host loss recovery fixture payload must never enter evidence"
CHILD_CHECKER = Path(__file__).with_name("check_worker_host_loss_queue_recovery_postgres.py")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="emit JSON evidence")
    parser.add_argument(
        "--iterations",
        type=int,
        default=int(os.getenv("AIAT_WORKER_HOST_LOSS_RECOVERY_SOAK_ITERATIONS", DEFAULT_ITERATIONS)),
        help=f"bounded sequential child-process iterations (1-{MAX_ITERATIONS})",
    )
    parser.add_argument(
        "--timeout-seconds",
        type=int,
        default=int(
            os.getenv(
                "AIAT_WORKER_HOST_LOSS_RECOVERY_SOAK_TIMEOUT_SECONDS",
                DEFAULT_TIMEOUT_SECONDS,
            )
        ),
        help=f"per-iteration child timeout ({MIN_TIMEOUT_SECONDS}-{MAX_TIMEOUT_SECONDS})",
    )
    parser.add_argument(
        "--dsn",
        default=os.getenv(
            "AIAT_WORKER_HOST_LOSS_QUEUE_RECOVERY_EVIDENCE_DSN",
            os.getenv("PGBOUNCER_DSN", os.getenv("POSTGRES_DSN", "")),
        ),
        help="Postgres DSN passed through an environment variable to each child",
    )
    parser.add_argument(
        "--checker-path",
        default=str(CHILD_CHECKER),
        help="path to check_worker_host_loss_queue_recovery_postgres.py",
    )
    return parser


def _blocked(reason: str) -> dict[str, Any]:
    return {
        "schema_version": CHECK_SCHEMA,
        "mode": "local-postgres-worker-host-loss-queue-recovery-soak",
        "status": "blocked",
        "reason": reason,
        "iteration_count": 0,
        "completed_iteration_count": 0,
        "mutation_performed": False,
        "local_database_access_performed": False,
        "external_network_access_performed": False,
        "external_provider_mutation_performed": False,
        "payload_free": True,
        "licence_metadata_is_gate": False,
    }


def _valid_dsn(value: str | None) -> bool:
    normalized = str(value or "").strip()
    return bool(normalized) and "${" not in normalized and "}" not in normalized


def _project_child(
    report: dict[str, Any],
    *,
    iteration: int,
    return_code: int,
    duration_ms: float,
) -> dict[str, Any]:
    """Keep only scalar child evidence; never retain child diagnostics/payloads."""

    recovery = report.get("worker_run_recovery")
    recovery = recovery if isinstance(recovery, dict) else {}
    durable = report.get("durable_reopen")
    durable = durable if isinstance(durable, dict) else {}
    remaining = report.get("remaining_fixture_counts")
    remaining = remaining if isinstance(remaining, dict) else {}
    scalar_remaining = {
        str(key): int(value)
        for key, value in remaining.items()
        if isinstance(value, (int, float)) and not isinstance(value, bool)
    }
    return {
        "iteration": iteration,
        "status": str(report.get("status", "fail")),
        "return_code": return_code,
        "duration_ms": round(duration_ms, 3),
        "run_state": str(report.get("run_state", "unknown")),
        "attempt_count": int(recovery.get("attempt_count", 0) or 0),
        "stale_executor_rejection": str(
            recovery.get("stale_executor_rejection", "unknown")
        ),
        "transition_count": int(report.get("transition_count", 0) or 0),
        "event_count": int(report.get("event_count", 0) or 0),
        "usage_count": int(report.get("usage_count", 0) or 0),
        "artifact_count": int(report.get("artifact_count", 0) or 0),
        "native_span_count": int(report.get("native_span_count", 0) or 0),
        "durable_reopen_healthy": bool(durable.get("healthy", False)),
        "payload_free": bool(report.get("payload_free", False)),
        "remaining_fixture_counts": scalar_remaining,
        "external_network_access_performed": bool(
            report.get("external_network_access_performed", True)
        ),
        "external_provider_mutation_performed": bool(
            report.get("external_provider_mutation_performed", True)
        ),
        "licence_metadata_is_gate": bool(report.get("licence_metadata_is_gate", True)),
    }


def _iteration_passed(row: dict[str, Any]) -> bool:
    remaining = row.get("remaining_fixture_counts")
    return all(
        (
            row.get("status") == "pass",
            row.get("return_code") == 0,
            row.get("run_state") == "SUCCEEDED",
            row.get("attempt_count") == 2,
            row.get("stale_executor_rejection") == "run_host_reservation_not_committed",
            row.get("transition_count", 0) >= 8,
            row.get("event_count", 0) >= 2,
            row.get("usage_count") == 1,
            row.get("artifact_count") == 1,
            row.get("native_span_count", 0) >= 3,
            row.get("durable_reopen_healthy") is True,
            row.get("payload_free") is True,
            isinstance(remaining, dict)
            and all(value == 0 for value in remaining.values()),
            row.get("external_network_access_performed") is False,
            row.get("external_provider_mutation_performed") is False,
            row.get("licence_metadata_is_gate") is False,
        )
    )


def _run(args: argparse.Namespace) -> dict[str, Any]:
    if not 1 <= int(args.iterations) <= MAX_ITERATIONS:
        return _blocked("iterations_out_of_bounds")
    if not MIN_TIMEOUT_SECONDS <= int(args.timeout_seconds) <= MAX_TIMEOUT_SECONDS:
        return _blocked("timeout_out_of_bounds")
    if not _valid_dsn(args.dsn):
        return _blocked("worker_host_loss_recovery_soak_database_not_configured")

    checker_path = Path(str(args.checker_path)).resolve()
    if not checker_path.is_file():
        return _blocked("worker_host_loss_recovery_checker_not_found")

    child_environment = os.environ.copy()
    child_environment["AIAT_WORKER_HOST_LOSS_QUEUE_RECOVERY_EVIDENCE_DSN"] = str(args.dsn)
    rows: list[dict[str, Any]] = []
    raw_output_free = True
    for iteration in range(1, int(args.iterations) + 1):
        started = time.monotonic()
        try:
            completed = subprocess.run(
                [sys.executable, str(checker_path), "--json"],
                check=False,
                capture_output=True,
                text=True,
                env=child_environment,
                timeout=int(args.timeout_seconds),
            )
            duration_ms = (time.monotonic() - started) * 1000
            stdout = completed.stdout
            stderr = completed.stderr
            if PAYLOAD_MARKER in stdout or PAYLOAD_MARKER in stderr:
                raw_output_free = False
            try:
                parsed = json.loads(stdout)
            except json.JSONDecodeError:
                parsed = {}
            if not isinstance(parsed, dict):
                parsed = {}
            row = _project_child(
                parsed,
                iteration=iteration,
                return_code=completed.returncode,
                duration_ms=duration_ms,
            )
        except subprocess.TimeoutExpired:
            row = {
                "iteration": iteration,
                "status": "blocked",
                "return_code": 2,
                "duration_ms": round((time.monotonic() - started) * 1000, 3),
                "reason_type": "TimeoutExpired",
                "payload_free": True,
                "remaining_fixture_counts": {},
                "external_network_access_performed": False,
                "external_provider_mutation_performed": False,
                "licence_metadata_is_gate": False,
            }
        except OSError as exc:
            row = {
                "iteration": iteration,
                "status": "fail",
                "return_code": 1,
                "duration_ms": round((time.monotonic() - started) * 1000, 3),
                "reason_type": type(exc).__name__,
                "payload_free": True,
                "remaining_fixture_counts": {},
                "external_network_access_performed": False,
                "external_provider_mutation_performed": False,
                "licence_metadata_is_gate": False,
            }
        rows.append(row)
        if not _iteration_passed(row) or not raw_output_free:
            break

    durations = [float(row.get("duration_ms", 0.0)) for row in rows]
    passed_iterations = sum(_iteration_passed(row) for row in rows)
    passed = (
        raw_output_free
        and len(rows) == int(args.iterations)
        and passed_iterations == int(args.iterations)
    )
    return {
        "schema_version": CHECK_SCHEMA,
        "mode": "local-postgres-worker-host-loss-queue-recovery-soak",
        "status": "pass" if passed else "fail",
        "iteration_count": int(args.iterations),
        "completed_iteration_count": len(rows),
        "passed_iteration_count": passed_iterations,
        "child_checker": checker_path.name,
        "separate_child_process_invocations": len(rows),
        "iterations": rows,
        "duration_ms": {
            "minimum": round(min(durations), 3) if durations else 0.0,
            "maximum": round(max(durations), 3) if durations else 0.0,
            "mean": round(sum(durations) / len(durations), 3) if durations else 0.0,
        },
        "mutation_performed": bool(rows),
        "local_database_access_performed": bool(rows),
        "external_network_access_performed": False,
        "external_provider_mutation_performed": False,
        "payload_free": raw_output_free and all(row.get("payload_free") is True for row in rows),
        "licence_metadata_is_gate": False,
        "scope": "sequential same-host Postgres worker-host loss/requeue/reassignment recovery soak",
        "certification_boundary": {
            "repeated_production_host_loss_checker": "checked",
            "separate_child_process_invocations": "checked",
            "durable_postgres_reopen_per_iteration": "checked",
            "scoped_zero_fixture_cleanup_per_iteration": "checked",
            "payload_free_scalar_projection": "checked",
            "independent_deployed_hosts": "not_checked",
            "split_brain_across_machines": "not_checked",
            "gvisor_or_firecracker": "not_checked",
            "external_provider_or_outage_recovery": "not_checked",
            "clean_host_or_disaster_recovery": "not_checked",
        },
        "notes": [
            "Each child invokes the production local host-loss/requeue/reassignment certificate; the parent retains scalar iteration summaries only.",
            "This is bounded same-Compose-host soak evidence, not independent-host, provider, sandbox, outage, or disaster-recovery evidence.",
        ],
    }


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    report = _run(args)
    if args.json:
        print(json.dumps(report, sort_keys=True, indent=2))
    else:
        print(f"worker host-loss queue recovery soak: {report['status']}")
    return {"pass": 0, "fail": 1, "blocked": 2}[str(report["status"])]


if __name__ == "__main__":
    raise SystemExit(main())

"""Verify asynchronous governed flow-task Worker Run binding semantics.

This deterministic fixture exercises the real shared state classifier and
context-binding helpers.  It proves that queued/running runs keep a task
active, terminal runs settle it, parallel bindings do not overwrite one
another, and unknown states fail closed.  It does not dispatch workers,
contact storage, or claim live canary/recovery evidence.  Resource licence or
restriction metadata is intentionally not a predicate here.
"""

from __future__ import annotations

import argparse
import json
import sys
from copy import deepcopy
from typing import Any

from mas_core.workflow.worker_binding import (
    WORKER_RUN_NONTERMINAL_STATES,
    WORKER_RUN_TERMINAL_STATES,
    bind_pending_worker_run,
    classify_worker_run_state,
    clear_worker_run_binding,
)

SCHEMA_VERSION = "aiat.flow-worker-binding.v1"


def _check(name: str, passed: bool, reason: str) -> dict[str, Any]:
    return {"name": name, "status": "pass" if passed else "fail", "reason": reason}


def build_report() -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    checks.append(
        _check(
            "nonterminal_states_remain_pending",
            all(classify_worker_run_state(state) == "pending" for state in WORKER_RUN_NONTERMINAL_STATES),
            "all known queued/claimed/running states remain active flow bindings",
        )
    )
    checks.append(
        _check(
            "terminal_states_settle",
            classify_worker_run_state("SUCCEEDED") == "succeeded"
            and all(
                classify_worker_run_state(state) == "failed"
                for state in WORKER_RUN_TERMINAL_STATES - {"SUCCEEDED"}
            ),
            "only terminal Worker Run states may settle a governed task",
        )
    )

    initial_context = {
        "project": "fixture",
        "active_worker_runs": {
            "branch_a": {"run_id": "run-a", "state": "RUNNING"},
        },
    }
    original_context = deepcopy(initial_context)
    bound_context = bind_pending_worker_run(
        initial_context,
        node_id="branch_b",
        run_id="run-b",
        state="QUEUED",
        dispatch_mode="queued",
    )
    settled_context = clear_worker_run_binding(bound_context, node_id="branch_b")
    checks.append(
        _check(
            "parallel_bindings_are_preserved",
            initial_context["active_worker_runs"] == {"branch_a": {"run_id": "run-a", "state": "RUNNING"}}
            and set(bound_context["active_worker_runs"]) == {"branch_a", "branch_b"}
            and settled_context["active_worker_runs"] == initial_context["active_worker_runs"]
            and initial_context == original_context,
            "binding and settlement copy context without mutating caller data",
        )
    )

    unknown = classify_worker_run_state("future_state")
    try:
        bind_pending_worker_run(
            {}, node_id="unknown", run_id="run-unknown", state="future_state"
        )
    except ValueError:
        unknown_rejected = True
    else:
        unknown_rejected = False
    checks.append(
        _check(
            "unknown_states_fail_closed",
            unknown == "unknown" and unknown_rejected,
            "unrecognized Worker Run states cannot be persisted as active bindings",
        )
    )

    status = "pass" if all(item["status"] == "pass" for item in checks) else "fail"
    return {
        "schema_version": SCHEMA_VERSION,
        "mode": "static",
        "status": status,
        "checks": checks,
        "mutation": {"storage": False, "worker_dispatch": False},
        "live": {"status": "not_checked", "reason": "static contract fixture"},
        "licence_metadata": {
            "recorded": False,
            "affects_discovery_install_activation_or_execution": False,
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="emit the complete report as JSON")
    parser.add_argument("--live", action="store_true", help="reserved; live evidence is not claimed")
    args = parser.parse_args(argv)
    if args.live:
        report = {
            "schema_version": SCHEMA_VERSION,
            "mode": "live",
            "status": "blocked",
            "reason": "live flow/worker binding evidence requires an explicitly selected operator canary",
            "mutation": {"storage": False, "worker_dispatch": False},
            "licence_metadata": {
                "recorded": False,
                "affects_discovery_install_activation_or_execution": False,
            },
        }
    else:
        report = build_report()
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(f"flow worker binding: {report['status']}")
    return 2 if report["status"] == "blocked" else (0 if report["status"] == "pass" else 1)


if __name__ == "__main__":
    sys.exit(main())

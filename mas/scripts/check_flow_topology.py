"""Run the deterministic flow control-topology contract fixture.

This check validates only the saved-definition graph contract.  It does not
start a flow, call workers, mutate storage, or claim live recovery evidence.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from mas_core.workflow import parse_flow_definition, validate_flow

FLOW_TOPOLOGY_SCHEMA = "aiat.flow-topology-check.v1"


def _definition(*, valid: bool) -> dict[str, Any]:
    branches = ["contain", "diagnose"] if valid else ["contain", "missing"]
    edges = [
        {"id": "start-fanout", "source": "start", "target": "fanout"},
        {"id": "fanout-contain", "source": "fanout", "target": "contain"},
    ]
    if valid:
        edges.extend(
            [
                {"id": "fanout-diagnose", "source": "fanout", "target": "diagnose"},
                {"id": "contain-join", "source": "contain", "target": "join"},
                {"id": "diagnose-join", "source": "diagnose", "target": "join"},
            ]
        )
    else:
        edges.append({"id": "contain-join", "source": "contain", "target": "join"})
    edges.append({"id": "join-end", "source": "join", "target": "end"})
    return {
        "nodes": [
            {"id": "start", "type": "start", "config": {}},
            {"id": "fanout", "type": "parallel", "config": {"branches": branches}},
            {"id": "contain", "type": "task", "config": {"action": "contain"}},
            {"id": "diagnose", "type": "task", "config": {"action": "diagnose"}},
            {"id": "join", "type": "join", "config": {}},
            {"id": "end", "type": "end", "config": {}},
        ],
        "edges": edges,
    }


def build_report() -> dict[str, Any]:
    valid_errors = validate_flow(parse_flow_definition(_definition(valid=True)))
    invalid_errors = validate_flow(parse_flow_definition(_definition(valid=False)))
    status = "pass" if not valid_errors and invalid_errors else "fail"
    return {
        "schema_version": FLOW_TOPOLOGY_SCHEMA,
        "status": status,
        "cases": {
            "valid_parallel_join": {"passed": not valid_errors, "error_count": len(valid_errors)},
            "invalid_parallel_join": {
                "passed": bool(invalid_errors),
                "error_count": len(invalid_errors),
                "error_codes": sorted(
                    {
                        "unknown_branch"
                        if "unknown nodes" in error
                        else "missing_branch_edge"
                        if "missing outgoing edges" in error
                        else "join_arity"
                        if "at least two incoming" in error
                        else "unreachable_node"
                        if "unreachable" in error or "disconnected" in error
                        else "other"
                        for error in invalid_errors
                    }
                ),
            },
        },
        "mutation_performed": False,
        "worker_dispatch_performed": False,
        "live_execution_status": "not_checked",
        "licence_metadata_is_gate": False,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="emit JSON")
    args = parser.parse_args(argv)
    report = build_report()
    if args.json:
        print(json.dumps(report, sort_keys=True, indent=2))
    else:
        print(f"flow topology: {report['status']}")
    return 0 if report["status"] == "pass" else 1


if __name__ == "__main__":
    sys.exit(main())

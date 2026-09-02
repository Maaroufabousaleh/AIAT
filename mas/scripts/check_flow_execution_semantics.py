"""Exercise deterministic flow traversal semantics without live workers.

This fixture drives the real flow engine through parallel fan-out, join
synchronization, switch routing, and unknown-switch blocking.  It is separate
from saved-definition topology validation: no worker, database, project, or
live flow instance is started or mutated.  Licence/restriction metadata is not
part of the execution predicate.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from mas_core.workflow import get_next_nodes, parse_flow_definition, validate_flow

FLOW_EXECUTION_SCHEMA = "aiat.flow-execution-semantics.v1"


def _parallel_join_definition() -> dict[str, Any]:
    return {
        "nodes": [
            {"id": "start", "type": "start", "config": {}},
            {"id": "fanout", "type": "parallel", "config": {"branches": ["a", "b"]}},
            {"id": "a", "type": "task", "config": {"action": "a"}},
            {"id": "b", "type": "task", "config": {"action": "b"}},
            {"id": "join", "type": "join", "config": {}},
            {"id": "end", "type": "end", "config": {}},
        ],
        "edges": [
            {"id": "start-fanout", "source": "start", "target": "fanout"},
            {"id": "fanout-a", "source": "fanout", "target": "a"},
            {"id": "fanout-b", "source": "fanout", "target": "b"},
            {"id": "a-join", "source": "a", "target": "join"},
            {"id": "b-join", "source": "b", "target": "join"},
            {"id": "join-end", "source": "join", "target": "end"},
        ],
    }


def _switch_definition() -> dict[str, Any]:
    return {
        "nodes": [
            {"id": "start", "type": "start", "config": {}},
            {
                "id": "switch",
                "type": "switch",
                "config": {"switch_key": "result", "switch_cases": {"ok": "ok", "fail": "fail"}},
            },
            {"id": "ok", "type": "task", "config": {"action": "ok"}},
            {"id": "fail", "type": "task", "config": {"action": "fail"}},
            {"id": "end", "type": "end", "config": {}},
        ],
        "edges": [
            {"id": "start-switch", "source": "start", "target": "switch"},
            {"id": "switch-ok", "source": "switch", "target": "ok"},
            {"id": "switch-fail", "source": "switch", "target": "fail"},
            {"id": "ok-end", "source": "ok", "target": "end"},
            {"id": "fail-end", "source": "fail", "target": "end"},
        ],
    }


def _ids(definition: Any, completed: set[str], context: dict[str, Any] | None = None) -> list[str]:
    result = get_next_nodes(definition, completed, set(), context)
    return result.node_ids if not result.is_blocked else []


def build_report() -> dict[str, Any]:
    parallel = parse_flow_definition(_parallel_join_definition())
    switch = parse_flow_definition(_switch_definition())
    parallel_errors = validate_flow(parallel)
    switch_errors = validate_flow(switch)

    parallel_cases = {
        "start": _ids(parallel, set()),
        "fanout": _ids(parallel, {"start"}),
        "branches": _ids(parallel, {"start", "fanout"}),
        "one_branch_waits": _ids(parallel, {"start", "fanout", "a"}),
        "join_once": _ids(parallel, {"start", "fanout", "a", "b"}),
        "end_after_join": _ids(parallel, {"start", "fanout", "a", "b", "join"}),
    }
    parallel_passed = (
        not parallel_errors
        and parallel_cases["start"] == ["start"]
        and parallel_cases["fanout"] == ["fanout"]
        and parallel_cases["branches"] == ["a", "b"]
        and parallel_cases["one_branch_waits"] == ["b"]
        and parallel_cases["join_once"] == ["join"]
        and parallel_cases["end_after_join"] == ["end"]
        and len(parallel_cases["join_once"]) == len(set(parallel_cases["join_once"]))
    )

    switch_cases = {
        "activation": _ids(switch, {"start"}),
        "ok": _ids(switch, {"start", "switch"}, {"result": "ok"}),
        "fail": _ids(switch, {"start", "switch"}, {"result": "fail"}),
    }
    unknown_result = get_next_nodes(switch, {"start", "switch"}, set(), {"result": "unknown"})
    switch_passed = (
        not switch_errors
        and switch_cases["activation"] == ["switch"]
        and switch_cases["ok"] == ["ok"]
        and switch_cases["fail"] == ["fail"]
        and unknown_result.is_blocked
    )
    checks = {
        "parallel_join": {"status": "pass" if parallel_passed else "fail", "cases": parallel_cases},
        "switch_routing": {
            "status": "pass" if switch_passed else "fail",
            "cases": switch_cases,
            "unknown_blocked": unknown_result.is_blocked,
        },
    }
    failed = [name for name, row in checks.items() if row["status"] != "pass"]
    return {
        "schema_version": FLOW_EXECUTION_SCHEMA,
        "status": "fail" if failed else "pass",
        "checks": checks,
        "failed_checks": failed,
        "mutation_performed": False,
        "worker_dispatch_performed": False,
        "live_execution_status": "not_checked",
        "licence_metadata_is_gate": False,
        "scope": "deterministic flow-engine traversal; no live instance or external state changed",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="emit JSON")
    args = parser.parse_args(argv)
    report = build_report()
    if args.json:
        print(json.dumps(report, sort_keys=True, indent=2))
    else:
        print(f"flow execution semantics: {report['status']}")
    return 0 if report["status"] == "pass" else 1


if __name__ == "__main__":
    sys.exit(main())

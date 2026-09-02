"""Run the deterministic AIAT worker placement/capacity contract fixture."""

from __future__ import annotations

import argparse
import json
from typing import Any

from mas_core.worker_registry.placement import (
    HostCapacity,
    WorkerHostSnapshot,
    WorkerPlacementRequest,
    build_placement_report,
)

CHECK_SCHEMA = "aiat.worker-placement-check.v1"


def _host(
    host_id: str,
    *,
    status: str = "READY",
    host_plane: str = "worker",
    zone: str = "a",
    lease_valid: bool = True,
    priority: int = 0,
    capabilities: frozenset[str] = frozenset({"native", "gpu"}),
    sandbox_profiles: frozenset[str] = frozenset({"standard", "gvisor"}),
    isolation_modes: frozenset[str] = frozenset({"native", "gvisor"}),
    capacity: HostCapacity | None = None,
) -> WorkerHostSnapshot:
    return WorkerHostSnapshot(
        host_id=host_id,
        status=status,
        host_plane=host_plane,
        labels=(("zone", zone),),
        capabilities=capabilities,
        sandbox_profiles=sandbox_profiles,
        isolation_modes=isolation_modes,
        capacity=capacity
        or HostCapacity(
            slots_total=4,
            slots_used=1,
            memory_bytes_total=8 * 1024**3,
            memory_bytes_used=1024**3,
            gpu_total=1,
            gpu_used=0,
        ),
        lease_valid=lease_valid,
        priority=priority,
    )


def build_report() -> dict[str, Any]:
    eligible_request = WorkerPlacementRequest(
        worker_id="fixture-worker",
        required_capabilities=frozenset({"native", "gpu"}),
        required_labels=(("zone", "a"),),
        required_sandbox_profile="gvisor",
        required_isolation_mode="gvisor",
        memory_bytes=1024**3,
        gpu_count=1,
        slots=1,
    )
    eligible = build_placement_report(
        hosts=(
            _host("host-b", priority=1),
            _host("host-a", priority=2),
            _host("draining", status="DRAINING"),
            _host("expired", lease_valid=False),
        ),
        request=eligible_request,
    )
    blocked = build_placement_report(
        hosts=(
            _host("full", capacity=HostCapacity(1, 1, 1024, 1024, 0, 0)),
            _host("wrong-zone", zone="b", capabilities=frozenset({"native"})),
            _host("control-plane", host_plane="control"),
        ),
        request=eligible_request,
    )
    duplicate = build_placement_report(
        hosts=(_host("duplicate"), _host("duplicate")),
        request=eligible_request,
    )
    eligible_pass = (
        eligible["status"] == "pass"
        and eligible["selected_host_id"] == "host-a"
        and eligible["eligible_host_count"] == 2
    )
    blocked_reasons = {
        reason
        for decision in blocked["decisions"]
        for reason in decision["reason_codes"]
    }
    blocked_pass = (
        blocked["status"] == "blocked"
        and blocked["selected_host_id"] is None
        and "host_plane_mismatch" in blocked_reasons
    )
    duplicate_pass = duplicate["status"] == "blocked" and duplicate["eligible_host_count"] == 0
    return {
        "schema_version": CHECK_SCHEMA,
        "placement_schema": eligible["schema_version"],
        "status": "pass" if eligible_pass and blocked_pass and duplicate_pass else "fail",
        "case_count": 3,
        "eligible_case": {
            "status": eligible["status"],
            "selected_host_id": eligible["selected_host_id"],
            "eligible_host_count": eligible["eligible_host_count"],
            "decision_count": eligible["decision_count"],
            "passed": eligible_pass,
        },
        "blocked_capacity_and_constraint_case": {
            "status": blocked["status"],
            "selected_host_id": blocked["selected_host_id"],
            "eligible_host_count": blocked["eligible_host_count"],
            "passed": blocked_pass,
        },
        "duplicate_host_id_case": {
            "status": duplicate["status"],
            "selected_host_id": duplicate["selected_host_id"],
            "eligible_host_count": duplicate["eligible_host_count"],
            "passed": duplicate_pass,
        },
        "mutation_performed": False,
        "external_network_access_performed": False,
        "external_provider_mutation_performed": False,
        "worker_dispatch_performed": False,
        "licence_metadata_is_gate": False,
        "scope": "deterministic placement constraints, host-plane separation, host health/lease filtering, and capacity ordering over explicit snapshots",
        "boundary": {
            "placement_predicate": "checked",
            "capacity_ordering": "checked",
            "duplicate_host_fail_closed": "checked",
            "worker_host_plane_isolation": "checked",
            "durable_host_registration": "not_checked",
            "live_multi_host_scheduler": "not_checked",
            "lease_reservation_commit": "not_checked",
            "host_loss_or_split_brain": "not_checked",
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="emit JSON")
    args = parser.parse_args()
    report = build_report()
    if args.json:
        print(json.dumps(report, sort_keys=True, indent=2))
    else:
        print(f"worker placement contract: {report['status']}")
    return 0 if report["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())

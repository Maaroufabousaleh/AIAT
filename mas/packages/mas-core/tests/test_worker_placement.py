from __future__ import annotations

from mas_core.worker_registry.placement import (
    HostCapacity,
    WorkerHostSnapshot,
    WorkerPlacementRequest,
    build_placement_report,
    select_host,
)


def _host(host_id: str, **overrides: object) -> WorkerHostSnapshot:
    values: dict[str, object] = {
        "host_id": host_id,
        "status": "READY",
        "labels": (("zone", "a"),),
        "capabilities": frozenset({"native", "gpu"}),
        "sandbox_profiles": frozenset({"standard", "gvisor"}),
        "isolation_modes": frozenset({"native", "gvisor"}),
        "capacity": HostCapacity(
            slots_total=4,
            slots_used=1,
            memory_bytes_total=8 * 1024**3,
            memory_bytes_used=1024**3,
            gpu_total=1,
            gpu_used=0,
        ),
        "lease_valid": True,
        "priority": 0,
    }
    values.update(overrides)
    return WorkerHostSnapshot(**values)  # type: ignore[arg-type]


def _request(**overrides: object) -> WorkerPlacementRequest:
    values: dict[str, object] = {
        "worker_id": "worker-1",
        "required_capabilities": frozenset({"native"}),
        "required_labels": (("zone", "a"),),
        "required_sandbox_profile": "gvisor",
        "required_isolation_mode": "gvisor",
        "memory_bytes": 1024**3,
        "gpu_count": 1,
        "slots": 1,
    }
    values.update(overrides)
    return WorkerPlacementRequest(**values)  # type: ignore[arg-type]


def test_select_host_is_deterministic_and_prefers_priority() -> None:
    selected, decisions = select_host(
        (_host("host-b", priority=1), _host("host-a", priority=2)),
        _request(),
    )

    assert selected == "host-a"
    assert [decision.host_id for decision in decisions] == ["host-b", "host-a"]
    assert all(decision.eligible for decision in decisions)


def test_placement_fails_closed_on_status_lease_and_constraints() -> None:
    report = build_placement_report(
        hosts=(
            _host("draining", status="DRAINING"),
            _host("expired", lease_valid=False),
            _host("wrong-zone", labels=(("zone", "b"),)),
        ),
        request=_request(),
    )

    assert report["status"] == "blocked"
    assert report["selected_host_id"] is None
    assert report["eligible_host_count"] == 0
    reasons = {reason for decision in report["decisions"] for reason in decision["reason_codes"]}
    assert {"host_not_ready", "host_lease_invalid", "placement_label_mismatch"} <= reasons


def test_placement_fails_closed_on_capacity() -> None:
    selected, decisions = select_host(
        (
            _host(
                "full",
                capacity=HostCapacity(
                    slots_total=1,
                    slots_used=1,
                    memory_bytes_total=1024,
                    memory_bytes_used=1024,
                    gpu_total=0,
                    gpu_used=0,
                ),
            ),
        ),
        _request(memory_bytes=2048, gpu_count=1),
    )

    assert selected is None
    assert decisions[0].reason_codes == (
        "capacity_slots_exhausted",
        "capacity_memory_exhausted",
        "capacity_gpu_exhausted",
    )


def test_duplicate_host_ids_fail_closed() -> None:
    selected, decisions = select_host((_host("same"), _host("same")), _request())

    assert selected is None
    assert all("host_registry_duplicate_id" in decision.reason_codes for decision in decisions)


def test_invalid_request_and_capacity_are_explicit() -> None:
    report = build_placement_report(
        hosts=(_host("host-a", capacity=HostCapacity(slots_total=-1)),),
        request=_request(slots=0),
    )

    assert report["status"] == "blocked"
    assert {"request_invalid", "capacity_invalid"} <= set(report["decisions"][0]["reason_codes"])
    assert report["mutation_performed"] is False
    assert report["licence_metadata_is_gate"] is False

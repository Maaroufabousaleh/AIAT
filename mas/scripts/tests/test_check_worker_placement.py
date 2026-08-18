from __future__ import annotations

from check_worker_placement import build_report


def test_worker_placement_checker_passes_all_bounded_cases() -> None:
    report = build_report()

    assert report["schema_version"] == "aiat.worker-placement-check.v1"
    assert report["placement_schema"] == "aiat.worker-placement.v1"
    assert report["status"] == "pass"
    assert report["case_count"] == 3
    assert report["eligible_case"]["passed"] is True
    assert report["blocked_capacity_and_constraint_case"]["passed"] is True
    assert report["duplicate_host_id_case"]["passed"] is True
    assert report["mutation_performed"] is False
    assert report["licence_metadata_is_gate"] is False

from __future__ import annotations

from pathlib import Path

from check_worker_host_recovery_postgres import build_report

MAS_ROOT = Path(__file__).resolve().parents[1]


def test_worker_host_recovery_checker_fails_closed_without_database_configuration() -> None:
    report = build_report(dsn="")

    assert report["status"] == "blocked"
    assert report["reason"] == "worker_host_recovery_evidence_database_not_configured"
    assert report["mutation_performed"] is False
    assert report["external_provider_mutation_performed"] is False
    assert report["worker_dispatch_performed"] is False
    assert report["licence_metadata_is_gate"] is False

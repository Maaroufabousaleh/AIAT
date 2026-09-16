"""Keep worker Postgres certification checks aligned with the migration head."""

from __future__ import annotations

from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1]
WORKER_POSTGRES_CHECKERS = (
    "check_flow_instance_recovery_postgres.py",
    "check_gateway_worker_mail_edge_postgres.py",
    "check_metric_series_many_projects.py",
    "check_self_improvement_postgres_evidence.py",
    "check_trace_retention_execution.py",
    "check_worker_host_execution_postgres.py",
    "check_worker_host_loss_queue_recovery_postgres.py",
    "check_worker_host_model_resolution_postgres.py",
    "check_worker_host_recovery_postgres.py",
    "check_worker_host_registry_postgres.py",
    "check_worker_host_reservations_postgres.py",
    "check_worker_host_scheduler_postgres.py",
    "check_worker_independent_process_execution_postgres.py",
    "check_worker_lease_recovery_postgres.py",
    "check_worker_multi_host_execution_postgres.py",
    "check_worker_run_host_binding_postgres.py",
    "check_worker_run_postgres_evidence.py",
    "check_worker_version_pinning_postgres.py",
)


def test_worker_postgres_checkers_require_runtime_binding_head() -> None:
    for name in WORKER_POSTGRES_CHECKERS:
        source = (SCRIPTS / name).read_text(encoding="utf-8")
        assert "0042_worker_run_host_binding" not in source, name
        assert "0045_worker_tool_effects" in source, name

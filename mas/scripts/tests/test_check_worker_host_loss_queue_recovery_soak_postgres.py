from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "check_worker_host_loss_queue_recovery_soak_postgres.py"
)


def _module():
    spec = importlib.util.spec_from_file_location(
        "check_worker_host_loss_queue_recovery_soak_postgres", SCRIPT
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _args(module, **overrides):
    values = {
        "iterations": 2,
        "timeout_seconds": 10,
        "dsn": "postgresql+asyncpg://user:pass@db/mas",
        "checker_path": str(module.CHILD_CHECKER),
    }
    values.update(overrides)
    return argparse.Namespace(**values)


def _child_report() -> dict[str, object]:
    return {
        "status": "pass",
        "run_state": "SUCCEEDED",
        "worker_run_recovery": {
            "attempt_count": 2,
            "stale_executor_rejection": "run_host_reservation_not_committed",
        },
        "transition_count": 8,
        "event_count": 2,
        "usage_count": 1,
        "artifact_count": 1,
        "native_span_count": 3,
        "durable_reopen": {"healthy": True},
        "payload_free": True,
        "remaining_fixture_counts": {
            "workers": 0,
            "runs": 0,
            "bindings": 0,
            "reservations": 0,
            "hosts": 0,
            "spans": 0,
        },
        "external_network_access_performed": False,
        "external_provider_mutation_performed": False,
        "licence_metadata_is_gate": False,
        "error": "fixture payload must never be projected",
    }


def test_soak_checker_blocks_without_database() -> None:
    module = _module()
    report = module._run(_args(module, dsn=""))
    assert report["schema_version"] == (
        "aiat.worker-host-loss-queue-recovery-soak-postgres-certification.v1"
    )
    assert report["status"] == "blocked"
    assert report["mutation_performed"] is False
    assert report["licence_metadata_is_gate"] is False


def test_soak_checker_rejects_unbounded_iterations_and_timeout() -> None:
    module = _module()
    assert module._run(_args(module, iterations=0))["reason"] == "iterations_out_of_bounds"
    assert module._run(_args(module, iterations=module.MAX_ITERATIONS + 1))["reason"] == (
        "iterations_out_of_bounds"
    )
    assert module._run(_args(module, timeout_seconds=module.MIN_TIMEOUT_SECONDS - 1))["reason"] == (
        "timeout_out_of_bounds"
    )


def test_project_child_keeps_scalar_recovery_fields_only() -> None:
    module = _module()
    projected = module._project_child(
        _child_report(), iteration=1, return_code=0, duration_ms=12.3456
    )
    assert projected["status"] == "pass"
    assert projected["duration_ms"] == 12.346
    assert projected["remaining_fixture_counts"]["runs"] == 0
    assert "error" not in projected


def test_soak_checker_repeats_children_and_projects_no_payload(monkeypatch) -> None:
    module = _module()
    calls: list[list[str]] = []
    child_json = json.dumps(_child_report())

    def fake_run(command, **_kwargs):
        calls.append(list(command))
        return SimpleNamespace(returncode=0, stdout=child_json, stderr="")

    monkeypatch.setattr(module.subprocess, "run", fake_run)
    report = module._run(_args(module))
    assert report["status"] == "pass"
    assert report["iteration_count"] == 2
    assert report["completed_iteration_count"] == 2
    assert report["passed_iteration_count"] == 2
    assert report["separate_child_process_invocations"] == 2
    assert report["payload_free"] is True
    assert report["licence_metadata_is_gate"] is False
    assert len(calls) == 2
    assert all(command[-1] == "--json" for command in calls)


def test_soak_checker_rejects_marker_in_child_output(monkeypatch) -> None:
    module = _module()

    def fake_run(_command, **_kwargs):
        return SimpleNamespace(
            returncode=0,
            stdout=module.PAYLOAD_MARKER,
            stderr="",
        )

    monkeypatch.setattr(module.subprocess, "run", fake_run)
    report = module._run(_args(module, iterations=1))
    assert report["status"] == "fail"
    assert report["payload_free"] is False

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from typing import Any

SCRIPT = Path(__file__).resolve().parents[1] / "opencode_vertical_e2e.py"
SPEC = importlib.util.spec_from_file_location("opencode_vertical_e2e", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
DRIVER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(DRIVER)


class _PendingWorkerAPI:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []
        self.project_deleted = False

    def request(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
    ) -> tuple[int, bytes, Any, dict[str, str]]:
        del payload
        self.calls.append((method, path))
        if method == "POST" and path == "/projects":
            return (
                201,
                b"",
                {
                    "id": "disposable-project",
                    "state": "IN_PROGRESS",
                    "company_id": "company-1",
                },
                {},
            )
        if method == "GET" and path == "/capabilities/workers":
            return (
                200,
                b"",
                [
                    {
                        "id": "worker-opencode",
                        "status": "INACTIVE",
                        "adapter_type": "opencode",
                        "evaluation_status": "compatibility_pending_governance",
                    }
                ],
                {},
            )
        if method == "GET" and path == "/projects/disposable-project":
            if self.project_deleted:
                raise DRIVER.APIError(404, "RESOURCE_NOT_FOUND")
            return (
                200,
                b"",
                {
                    "id": "disposable-project",
                    "state": "IN_PROGRESS",
                    "company_id": "company-1",
                },
                {},
            )
        if method == "GET" and path == "/companies/company-1":
            return 200, b"", {"company": {"id": "company-1", "status": "ACTIVE"}}, {}
        if method == "GET" and path == "/companies/company-1/assignments":
            return 200, b"", [], {}
        if method == "GET" and path == "/companies/company-1/budgets":
            return 200, b"", [], {}
        if method == "GET" and path == "/model-profiles":
            return 200, b"", [], {}
        if method == "GET" and path == "/capabilities/workers/worker-opencode/health":
            return 200, b"", {"health_status": "healthy"}, {}
        if method == "DELETE" and path == "/projects/disposable-project":
            self.project_deleted = True
            return 204, b"", None, {}
        raise AssertionError(f"unexpected API call: {method} {path}")


def test_pending_worker_gate_still_cleans_disposable_project(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    api = _PendingWorkerAPI()
    monkeypatch.setattr(
        DRIVER,
        "_verify_local_fixture_environment",
        lambda: (api, "worker-opencode"),
    )
    evidence = tmp_path / "evidence.json"

    exit_code = DRIVER.main(["--evidence", str(evidence)])

    report = json.loads(evidence.read_text(encoding="utf-8"))
    stages = report["stages"]
    assert exit_code == 2
    assert stages["WORKER_SELECTED"]["status"] == "BLOCKED"
    assert stages["WORKER_SELECTED"]["reason"] == "CANONICAL_WORKER_READINESS_BLOCKED"
    blocker_codes = stages["WORKER_SELECTED"]["evidence"]["readiness"]["blocker_codes"]
    assert "worker_not_active" in blocker_codes
    assert "worker_evaluation_not_approved" in blocker_codes
    assert "active_shell_version_id_missing" in blocker_codes
    assert "active_adapter_id_missing" in blocker_codes
    assert "active_skill_bundle_id_missing" in blocker_codes
    assert stages["WORKER_RUN_SUBMITTED"]["status"] == "NOT_RUN"
    assert stages["HOST_CONSUMER_EXECUTED"]["status"] == "NOT_RUN"
    assert stages["CLEANUP_VERIFIED"]["status"] == "PASS"
    assert ("DELETE", "/projects/disposable-project") in api.calls
    assert not any(method == "POST" and path == "/workers/runs" for method, path in api.calls)


def _valid_binding() -> dict[str, Any]:
    host_id = "aiat-level-a-host-abc123"
    return {
        "id": "binding-1",
        "reservation_id": "reservation-1",
        "host_uuid": "host-uuid-1",
        "host_id": host_id,
        "owner": f"aiat-local-host:{host_id}",
        "state": "RELEASED",
        "reservation_state": "RELEASED",
        "committed_at": "2026-10-12T12:00:00Z",
        "released_at": "2026-10-12T12:01:00Z",
    }


def _valid_host_transitions() -> list[dict[str, Any]]:
    return [
        {
            "id": "transition-claimed",
            "from_state": "QUEUED",
            "to_state": "CLAIMED",
            "actor": "aiat-local-host:aiat-level-a-host-abc123",
        },
        {
            "id": "transition-validating",
            "from_state": "CLAIMED",
            "to_state": "VALIDATING",
            "actor": "worker-run-controller",
        },
        {
            "id": "transition-ready",
            "from_state": "VALIDATING",
            "to_state": "READY",
            "actor": "worker-run-controller",
        },
        {
            "id": "transition-dispatching",
            "from_state": "READY",
            "to_state": "DISPATCHING",
            "actor": "worker-run-controller",
        },
        {
            "id": "transition-running",
            "from_state": "DISPATCHING",
            "to_state": "RUNNING",
            "actor": "worker-run-controller",
        },
    ]


def test_host_consumer_evidence_requires_bound_host_claim_and_controller_run() -> None:
    status, reason, evidence = DRIVER._host_consumer_execution_evidence(
        _valid_binding(),
        _valid_host_transitions(),
        run_id="run-1",
        expected_state="SUCCEEDED",
    )

    assert (status, reason) == ("PASS", None)
    assert evidence["host_id"] == "aiat-level-a-host-abc123"
    assert evidence["claim_preceded_running"] is True
    assert evidence["host_health_corroboration"] == "NOT_OBSERVED"


def test_host_health_summary_cannot_replace_durable_host_claim() -> None:
    transitions = _valid_host_transitions()
    transitions[0]["actor"] = "worker-run-controller"
    status, reason, _ = DRIVER._host_consumer_execution_evidence(
        _valid_binding(),
        transitions,
        run_id="run-1",
        expected_state="SUCCEEDED",
        host_health={
            "host_id": "aiat-level-a-host-abc123",
            "last_runs": [{"run_id": "run-1", "state": "SUCCEEDED"}],
        },
    )

    assert status == "FAIL"
    assert reason == "HOST_CLAIM_TRANSITION_NOT_FOUND"


def test_host_claim_from_another_process_identity_does_not_match_binding() -> None:
    transitions = _valid_host_transitions()
    transitions[0]["actor"] = "aiat-local-host:aiat-level-a-host-replacement"

    status, reason, _ = DRIVER._host_consumer_execution_evidence(
        _valid_binding(),
        transitions,
        run_id="run-1",
        expected_state="SUCCEEDED",
    )

    assert status == "FAIL"
    assert reason == "HOST_CLAIM_TRANSITION_NOT_FOUND"


def test_project_evidence_must_be_complete_and_link_verified_artifacts() -> None:
    package = {
        "schema_version": "aiat.project-evidence-package.v1",
        "project_id": "project-1",
        "policy_id": "software_delivery",
        "status": "complete",
        "evidence_refs": {"artifacts": ["artifact-1", "artifact-2"]},
        "checks": [
            {"name": "worker_runs_terminal", "required": True, "passed": True},
            {"name": "tests", "required": False, "passed": False},
        ],
    }

    ready, reason, summary = DRIVER._project_evidence_readiness(
        package,
        project_id="project-1",
        artifact_ids={"artifact-1", "artifact-2"},
    )

    assert ready is True
    assert reason == ""
    assert summary["artifact_ref_count"] == 2


def test_project_evidence_rejects_incomplete_policy_or_unlinked_artifacts() -> None:
    package = {
        "schema_version": "aiat.project-evidence-package.v1",
        "project_id": "project-1",
        "policy_id": "software_delivery",
        "status": "incomplete",
        "evidence_refs": {"artifacts": ["artifact-1"]},
        "checks": [
            {"name": "worker_runs_terminal", "required": True, "passed": True},
            {"name": "documents_exist", "required": True, "passed": False},
        ],
    }

    ready, reason, _ = DRIVER._project_evidence_readiness(
        package,
        project_id="project-1",
        artifact_ids={"artifact-1", "artifact-2"},
    )

    assert ready is False
    assert reason == "PROJECT_EVIDENCE_POLICY_INCOMPLETE"


def test_project_evidence_rejects_cross_project_package() -> None:
    package = {
        "schema_version": "aiat.project-evidence-package.v1",
        "project_id": "other-project",
        "policy_id": "software_delivery",
        "status": "complete",
        "evidence_refs": {"artifacts": ["artifact-1"]},
        "checks": [{"name": "worker_runs_terminal", "required": True, "passed": True}],
    }

    ready, reason, _ = DRIVER._project_evidence_readiness(
        package,
        project_id="project-1",
        artifact_ids={"artifact-1"},
    )

    assert ready is False
    assert reason == "PROJECT_EVIDENCE_SCOPE_MISMATCH"


def test_host_health_mismatch_is_not_recorded_as_corroboration() -> None:
    status, reason, evidence = DRIVER._host_consumer_execution_evidence(
        _valid_binding(),
        _valid_host_transitions(),
        run_id="run-1",
        expected_state="SUCCEEDED",
        host_health={
            "host_id": "different-host",
            "last_runs": [{"run_id": "run-1", "state": "SUCCEEDED"}],
        },
    )

    assert (status, reason) == ("PASS", None)
    assert evidence["host_health_corroboration"] == "MISMATCHED"

from __future__ import annotations

import hashlib
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
                        "name": "coding_worker",
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
        lambda: (api, "coding_worker"),
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


class _ReadinessAPI:
    def __init__(self, worker: dict[str, Any]) -> None:
        self.worker = worker
        self.calls: list[str] = []

    def request(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
    ) -> tuple[int, bytes, Any, dict[str, str]]:
        del payload
        assert method == "GET"
        self.calls.append(path)
        if path == "/capabilities/workers":
            return 200, b"", [self.worker], {}
        if path == "/projects/project-1":
            return 200, b"", {"id": "project-1", "company_id": "company-1"}, {}
        if path == "/companies/company-1":
            return 200, b"", {"company": {"id": "company-1"}}, {}
        if path == "/companies/company-1/assignments":
            return 200, b"", [], {}
        if path == "/companies/company-1/budgets":
            return 200, b"", [], {}
        if path == "/model-profiles":
            return 200, b"", [], {}
        if path == "/capabilities/workers/worker-1/health":
            return 200, b"", {"health_status": "healthy"}, {}
        raise AssertionError(f"unexpected readiness request: {path}")


def _readiness_result() -> dict[str, Any]:
    return {
        "schema_version": "aiat.worker-run-readiness.v1",
        "status": "pass",
        "blockers": [],
        "checks": {
            "worker": {
                "status": "ACTIVE",
                "evaluation_status": "approved",
                "model_mode": "none",
                "immutable_pointers": {},
            },
            "project": {"state": "INIT"},
            "assignment": {"status": "assigned"},
            "sandbox": {"status": "pass"},
            "budgets": {"status": "pass"},
        },
        "fetch_status": {},
    }


def test_model_free_readiness_uses_shared_gate_without_model_profiles(
    monkeypatch: Any,
) -> None:
    worker = {
        "id": "worker-1",
        "name": "aiat_deterministic_file_worker",
        "model_mode": "none",
    }
    api = _ReadinessAPI(worker)
    captured: dict[str, Any] = {}

    def evaluate(**kwargs: Any) -> dict[str, Any]:
        captured.update(kwargs)
        return _readiness_result()

    monkeypatch.setattr(
        "mas_core.worker_registry.worker_run_readiness.evaluate_worker_run_readiness",
        evaluate,
    )

    result = DRIVER._canonical_worker_readiness(
        api,
        worker_id="worker-1",
        worker_name="aiat_deterministic_file_worker",
        project_id="project-1",
    )

    assert result["status"] == "pass"
    assert captured["require_model_backed"] is False
    assert captured["model_profiles"] == []
    assert "/model-profiles" not in api.calls


def test_model_backed_readiness_remains_required_for_opencode(monkeypatch: Any) -> None:
    worker = {"id": "worker-1", "name": "coding_worker", "model_mode": "aiat_gateway"}
    api = _ReadinessAPI(worker)
    captured: dict[str, Any] = {}

    def evaluate(**kwargs: Any) -> dict[str, Any]:
        captured.update(kwargs)
        return _readiness_result()

    monkeypatch.setattr(
        "mas_core.worker_registry.worker_run_readiness.evaluate_worker_run_readiness",
        evaluate,
    )

    DRIVER._canonical_worker_readiness(
        api,
        worker_id="worker-1",
        worker_name="coding_worker",
        project_id="project-1",
    )

    assert captured["require_model_backed"] is True
    assert "/model-profiles" in api.calls


class _DeterministicRunAPI:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict[str, Any] | None]] = []
        self.project_deleted = False
        self.project_state = "INIT"
        self.evidence_complete = False
        self.dispatch: dict[str, Any] | None = None
        self.file_bytes = DRIVER.DETERMINISTIC_FILE_CONTENT.encode("utf-8")

    def request(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
    ) -> tuple[int, bytes, Any, dict[str, str]]:
        self.calls.append((method, path, payload))
        if method == "POST" and path == "/projects":
            return 201, b"", {
                "id": "project-1",
                "state": "INIT",
                "company_id": "company-1",
            }, {}
        if method == "GET" and path == "/capabilities/workers":
            return 200, b"", [
                {
                    "id": "worker-1",
                    "name": "aiat_deterministic_file_worker",
                    "status": "ACTIVE",
                    "adapter_type": "oci",
                    "model_mode": "none",
                    "evaluation_status": "approved",
                }
            ], {}
        if method == "POST" and path == "/workers/runs":
            self.dispatch = payload
            return (
                202,
                b"",
                {"run_id": "run-1", "state": "QUEUED", "dispatch_mode": "queued"},
                {},
            )
        if method == "GET" and path == "/workers/runs/run-1":
            return 200, b"", {"run_id": "run-1", "state": "SUCCEEDED"}, {}
        if method == "GET" and path == "/workers/runs/run-1/host-binding":
            return 200, b"", {"host_binding": _valid_binding()}, {}
        if method == "GET" and path == "/workers/runs/run-1/transitions":
            return 200, b"", _valid_host_transitions(), {}
        if method == "GET" and path == "/workers/host/local/health":
            return 200, b"", {
                "host_id": "aiat-level-a-host-abc123",
                "last_runs": [{"run_id": "run-1", "state": "SUCCEEDED"}],
            }, {}
        if method == "GET" and path == "/workers/runs/run-1/events?limit=1000":
            return 200, b"", [], {}
        if method == "GET" and path == "/workers/runs/run-1/tool-effects":
            return 200, b"", {
                "tool_effects": [
                    {
                        "effect_id": "effect-1",
                        "tool_name": "file_write",
                        "state": "COMPLETED",
                        "response_success": True,
                    }
                ]
            }, {}
        if method == "GET" and path == "/workers/runs/run-1/artifacts":
            return 200, b"", [
                {
                    "artifact_id": "artifact-1",
                    "uri": "project://project-1/expenses.py",
                    "sha256": hashlib.sha256(self.file_bytes).hexdigest(),
                }
            ], {}
        if method == "GET" and path == "/projects/project-1/artifacts/artifact-1/download":
            digest = hashlib.sha256(self.file_bytes).hexdigest()
            return 200, self.file_bytes, None, {"X-AIAT-Artifact-SHA256": digest}
        if method == "GET" and path == "/projects/project-1/evidence/package":
            return 200, b"", {
                "schema_version": "aiat.project-evidence-package.v1",
                "project_id": "project-1",
                "policy_id": "software_delivery",
                "status": "complete" if self.evidence_complete else "incomplete",
                "evidence_refs": {"artifacts": ["artifact-1"]},
                "checks": [
                    {
                        "name": "human_review",
                        "required": True,
                        "passed": self.evidence_complete,
                    }
                ],
            }, {}
        if method == "GET" and path == "/projects/project-1":
            if self.project_deleted:
                raise DRIVER.APIError(404, "RESOURCE_NOT_FOUND")
            return 200, b"", {"id": "project-1", "state": self.project_state}, {}
        if method == "DELETE" and path == "/projects/project-1":
            self.project_deleted = True
            return 204, b"", None, {}
        raise AssertionError(f"unexpected API call: {method} {path}")


def test_deterministic_worker_real_api_driver_contract_stops_at_completion_policy(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    api = _DeterministicRunAPI()
    monkeypatch.setattr(
        DRIVER,
        "_verify_local_fixture_environment",
        lambda: (api, "aiat_deterministic_file_worker"),
    )
    monkeypatch.setattr(
        DRIVER,
        "_canonical_worker_readiness",
        lambda *args, **kwargs: {"status": "pass"},
    )
    evidence = tmp_path / "deterministic-evidence.json"

    exit_code = DRIVER.main(["--timeout-seconds", "30", "--evidence", str(evidence)])
    report = json.loads(evidence.read_text(encoding="utf-8"))
    stages = report["stages"]

    assert exit_code == 2
    assert report["worker_name"] == "aiat_deterministic_file_worker"
    assert report["provider_dispatch"] == "not_applicable_non_model"
    assert stages["WORKER_SELECTED"]["evidence"]["worker_id"] == "worker-1"
    assert (
        stages["WORKER_SELECTED"]["evidence"]["worker_name"]
        == "aiat_deterministic_file_worker"
    )
    assert api.dispatch is not None
    assert api.dispatch["task_type"] == "deterministic_file_generation"
    assert api.dispatch["task_input"] == {
        "path": DRIVER.DETERMINISTIC_FILE_PATH,
        "content": DRIVER.DETERMINISTIC_FILE_CONTENT,
    }
    assert api.dispatch["tool_grants"] == ["file_write"]
    assert api.dispatch["permission_requirements"] == ["project.workspace.write"]
    assert stages["MODEL_REQUEST_RESOLVED"] == {
        "status": "N/A",
        "reason": "NON_MODEL_WORKER",
    }
    assert stages["LOCAL_MODEL_RESPONSE_RECEIVED"] == {
        "status": "N/A",
        "reason": "NON_MODEL_WORKER",
    }
    for stage in (
        "PROJECT_CREATED",
        "WORKER_SELECTED",
        "WORKER_RUN_SUBMITTED",
        "HOST_RESERVED",
        "HOST_CLAIMED",
        "HOST_CONSUMER_EXECUTED",
        "RUNTIME_STARTED",
        "TOOL_SERVICE_CALLED",
        "TOOL_EFFECT_PERSISTED",
        "FILE_GENERATED",
        "ARTIFACT_REGISTERED",
        "WORKER_RUN_SETTLED",
        "EVIDENCE_AVAILABLE",
    ):
        assert stages[stage]["status"] == "PASS", stage
    expected_hash = hashlib.sha256(api.file_bytes).hexdigest()
    assert stages["FILE_GENERATED"]["evidence"]["sha256"]["expenses.py"] == expected_hash
    assert stages["ARTIFACT_REGISTERED"]["evidence"]["artifacts"][0]["sha256"] == expected_hash
    assert stages["EVIDENCE_AVAILABLE"]["evidence"]["artifact_ids"] == ["artifact-1"]
    assert stages["PROJECT_COMPLETED"]["status"] == "BLOCKED"
    assert (
        stages["PROJECT_COMPLETED"]["reason"]
        == "PROJECT_WORKFLOW_COMPLETION_REQUIRES_ITS_GOVERNED_MILESTONES"
    )
    assert stages["PROJECT_COMPLETED"]["evidence"]["evidence_chain_passed"] is True
    assert stages["PROJECT_COMPLETED"]["evidence"]["completion_policy_ready"] is False
    assert stages["CLEANUP_VERIFIED"]["status"] == "PASS"
    assert api.project_deleted is True
    assert not any(method == "POST" and path.endswith("/transition") for method, path, _ in api.calls)


def test_driver_does_not_emit_kpi_saved_when_kpi_computation_is_required(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    api = _DeterministicRunAPI()
    api.project_state = "KPI_PERSISTENCE"
    api.evidence_complete = True
    monkeypatch.setattr(
        DRIVER,
        "_verify_local_fixture_environment",
        lambda: (api, "aiat_deterministic_file_worker"),
    )
    monkeypatch.setattr(
        DRIVER,
        "_canonical_worker_readiness",
        lambda *args, **kwargs: {"status": "pass"},
    )
    evidence = tmp_path / "completed-project-evidence.json"

    exit_code = DRIVER.main(["--timeout-seconds", "30", "--evidence", str(evidence)])
    stages = json.loads(evidence.read_text(encoding="utf-8"))["stages"]

    assert exit_code == 2
    assert stages["EVIDENCE_AVAILABLE"]["status"] == "PASS"
    assert stages["PROJECT_COMPLETED"]["status"] == "BLOCKED"
    assert stages["PROJECT_COMPLETED"]["reason"] == "PROJECT_KPI_COMPUTATION_REQUIRED"
    assert stages["PROJECT_COMPLETED"]["evidence"]["state"] == "KPI_PERSISTENCE"
    assert stages["PROJECT_COMPLETED"]["evidence"]["required_next_action"] == (
        "executive.kpi.compute_project"
    )
    assert not any(method == "POST" and path.endswith("/transition") for method, path, _ in api.calls)


def test_driver_passes_completion_only_after_completed_state_and_evidence_readback(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    api = _DeterministicRunAPI()
    api.project_state = "COMPLETED"
    api.evidence_complete = True
    monkeypatch.setattr(
        DRIVER,
        "_verify_local_fixture_environment",
        lambda: (api, "aiat_deterministic_file_worker"),
    )
    monkeypatch.setattr(
        DRIVER,
        "_canonical_worker_readiness",
        lambda *args, **kwargs: {"status": "pass"},
    )
    evidence = tmp_path / "completed-project-readback.json"

    exit_code = DRIVER.main(["--timeout-seconds", "30", "--evidence", str(evidence)])
    stages = json.loads(evidence.read_text(encoding="utf-8"))["stages"]

    assert exit_code == 0
    assert stages["EVIDENCE_AVAILABLE"]["status"] == "PASS"
    assert stages["PROJECT_COMPLETED"]["status"] == "PASS"
    assert stages["PROJECT_COMPLETED"]["evidence"]["state"] == "COMPLETED"
    assert stages["PROJECT_COMPLETED"]["evidence"]["completion_policy_ready"] is True
    assert not any(method == "POST" and path.endswith("/transition") for method, path, _ in api.calls)

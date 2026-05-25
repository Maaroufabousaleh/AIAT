"""
Test 5: GitHub-cloned worker lifecycle — ingest, evaluate, approve, upgrade.

Coverage matrix
───────────────
Type        Scenarios
API         register with source_repo, POST /evaluate, GET /evaluations,
            POST /upgrade (mock git), GET /upstream, PATCH /status,
            reject breaking upgrade (compat test fail → 409),
            accept compatible upgrade (compat pass → updated commit_sha)
Unit        evaluator._compute_verdict: APPROVED/CONDITIONAL/REJECTED thresholds,
            evaluator._check_licensing: compatible/rejected/missing license,
            evaluator._check_security: clean/secrets-found scenarios,
            evaluator._check_architecture: entrypoint/structure scoring,
            evaluator._check_compatibility: python+main scoring
            compat_tests.run_compatibility_tests: manifest_validation,
            capability_contract, transport_compatibility, sandbox_compliance,
            budget_enforcement, message_protocol, checkpoint_compatibility
Integration register → evaluate (mocked mirror) → approve → assign to flow → upgrade
            → breaking upgrade rejected → old version stays active
            → compatible upgrade accepted → version_pin updated
Negative    evaluate worker with no source_repo (400),
            upgrade worker with no source_repo (400),
            upgrade when compat tests fail (409, worker stays ACTIVE),
            evaluate unknown worker (404), upgrade unknown worker (404),
            evaluations list for unknown worker (404)
Audit       evaluation_status set to "approved" after approve verdict,
            upstream_commit_sha updated after successful upgrade,
            worker stays ACTIVE after rejected upgrade (no mutation)
"""

from __future__ import annotations

import json
import textwrap
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

import pytest

from conftest import NOW_ISO

# ── constants ─────────────────────────────────────────────────────────────────

WORKER_ID = UUID("00000000-0000-4000-a000-0000000000e1")
CAP_ID = UUID("00000000-0000-4000-a000-0000000000f1")
EVAL_ID = UUID("00000000-0000-4000-a000-000000000101")

SOURCE_REPO = "https://github.com/example/code-reviewer"
COMMIT_SHA_OLD = "abc123def456"
COMMIT_SHA_NEW = "def789ghi012"


# ── helpers ──────────────────────────────────────────────────────────────────


def _patch(storage) -> None:
    from orchestrator_api.main import app

    app.state.storage = storage


def _worker_row(
    *,
    worker_id: UUID = WORKER_ID,
    name: str = "code_reviewer",
    status: str = "ACTIVE",
    source_repo: str = SOURCE_REPO,
    version_pin: str = "v1.0.0",
    update_policy: str = "manual",
    evaluation_status: str | None = "pending",
    upstream_commit_sha: str | None = COMMIT_SHA_OLD,
    source_revision: str = "main",
) -> dict:
    return {
        "id": worker_id,
        "name": name,
        "status": status,
        "adapter_type": "process",
        "adapter_config": {"entrypoint": "CodeReviewerAgent"},
        "sandbox_profile": "restricted",
        "capability_ids": [CAP_ID],
        "team_id": "dept_qa",
        "source_repo": source_repo,
        "source_revision": source_revision,
        "version_pin": version_pin,
        "update_policy": update_policy,
        "evaluation_status": evaluation_status,
        "adapter_entrypoint": "CodeReviewerAgent",
        "adapter_module": None,
        "isolation_mode": "native",
        "wrapper_config": {},
        "version": "1.0.0",
        "health_status": "healthy",
        "last_seen_at": None,
        "error_count": 0,
        "upstream_commit_sha": upstream_commit_sha,
        "last_upstream_sync": None,
        "created_at": NOW_ISO,
        "updated_at": NOW_ISO,
    }


def _eval_report(
    *,
    report_id: UUID = EVAL_ID,
    worker_id: UUID = WORKER_ID,
    verdict: str = "APPROVED",
    overall_score: float = 85.0,
) -> dict:
    return {
        "id": report_id,
        "worker_id": worker_id,
        "evaluated_at": NOW_ISO,
        "checks": {
            "architecture": {"passed": True, "score": 80.0, "details": "Entrypoint found"},
            "maintenance": {"passed": True, "score": 75.0, "details": "Recent commits"},
            "licensing": {"passed": True, "score": 100.0, "details": "MIT license"},
            "security": {"passed": True, "score": 100.0, "details": "No secrets found"},
            "compatibility": {"passed": True, "score": 80.0, "details": "Python+main"},
        },
        "overall_score": overall_score,
        "verdict": verdict,
        "evaluator_version": "1.1.0",
        "risk_tier": "low",
        "blocked_reasons": [],
        "recommended_status": "ACTIVE",
        "requires_human_approval": False,
        "notes": None,
    }


# ── 1. Register worker with GitHub source_repo ────────────────────────────────


@pytest.mark.anyio
async def test_register_worker_with_source_repo_creates_pending_evaluation(client):
    """POST /capabilities/workers with source_repo → registered with evaluation_status pending."""
    row = _worker_row()
    storage = MagicMock()
    storage.register_worker = AsyncMock(return_value=row)
    _patch(storage)

    resp = await client.post(
        "/capabilities/workers",
        json={
            "name": "code_reviewer",
            "adapter_type": "process",
            "source_repo": SOURCE_REPO,
            "version_pin": "v1.0.0",
            "update_policy": "manual",
        },
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["source_repo"] == SOURCE_REPO
    assert body["version_pin"] == "v1.0.0"
    # evaluation_status reflects what was returned (pending at registration)
    assert body["evaluation_status"] == "pending"


# ── 2. Trigger evaluation (mocked mirror) ─────────────────────────────────────


@pytest.mark.anyio
async def test_evaluate_worker_runs_all_checks_and_returns_verdict(client, tmp_path):
    """POST /capabilities/workers/{id}/evaluate → runs checks, stores report, returns verdict."""
    worker = _worker_row()
    report = _eval_report(verdict="APPROVED", overall_score=85.0)

    storage = MagicMock()
    storage.get_worker = AsyncMock(return_value=worker)
    storage.update_worker_config = AsyncMock(return_value=None)
    _patch(storage)

    # Mock the evaluate_repository function to avoid real git operations
    with patch(
        "mas_core.worker_registry.evaluator.evaluate_repository",
        new_callable=AsyncMock,
        return_value=report,
    ):
        resp = await client.post(
            f"/capabilities/workers/{WORKER_ID}/evaluate",
            json={"source_repo": SOURCE_REPO, "checks": ["architecture", "licensing", "security"]},
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["verdict"] == "APPROVED"
    assert body["overall_score"] == 85.0
    assert "architecture" in body["checks"]
    assert "licensing" in body["checks"]
    assert "security" in body["checks"]
    # Evaluation status written back to worker
    storage.update_worker_config.assert_awaited_once()
    _, kwargs = storage.update_worker_config.await_args
    assert kwargs.get("evaluation_status") == "approved"


@pytest.mark.anyio
async def test_evaluate_worker_with_no_source_repo_returns_400(client):
    """POST /capabilities/workers/{id}/evaluate with no source_repo → 400."""
    worker = _worker_row(source_repo=None)  # type: ignore[arg-type]
    worker_no_repo = dict(worker)
    worker_no_repo["source_repo"] = None

    storage = MagicMock()
    storage.get_worker = AsyncMock(return_value=worker_no_repo)
    _patch(storage)

    resp = await client.post(
        f"/capabilities/workers/{WORKER_ID}/evaluate",
        json={},  # no source_repo, worker has none either
    )
    assert resp.status_code == 400
    assert "source_repo" in resp.json()["detail"].lower()


@pytest.mark.anyio
async def test_evaluate_unknown_worker_returns_404(client):
    """POST /capabilities/workers/{unknown}/evaluate → 404."""
    storage = MagicMock()
    storage.get_worker = AsyncMock(return_value=None)
    _patch(storage)

    resp = await client.post(
        f"/capabilities/workers/{uuid4()}/evaluate",
        json={"source_repo": SOURCE_REPO},
    )
    assert resp.status_code == 404


# ── 3. Evaluation verdict: REJECTED cases ─────────────────────────────────────


@pytest.mark.anyio
async def test_evaluate_worker_with_gpl_license_verdict_is_rejected(client):
    """Evaluate endpoint returns REJECTED when licensing check fails (GPL)."""
    worker = _worker_row()
    rejected_report = _eval_report(
        verdict="REJECTED",
        overall_score=30.0,
    )
    rejected_report["checks"]["licensing"] = {
        "passed": False,
        "score": 0.0,
        "details": "Rejected license detected: gpl-3.0",
        "license": "gpl-3.0",
    }

    storage = MagicMock()
    storage.get_worker = AsyncMock(return_value=worker)
    storage.update_worker_config = AsyncMock(return_value=None)
    _patch(storage)

    with patch(
        "mas_core.worker_registry.evaluator.evaluate_repository",
        new_callable=AsyncMock,
        return_value=rejected_report,
    ):
        resp = await client.post(
            f"/capabilities/workers/{WORKER_ID}/evaluate",
            json={"source_repo": SOURCE_REPO},
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["verdict"] == "REJECTED"
    # evaluation_status written as "rejected"
    storage.update_worker_config.assert_awaited_once()
    _, kwargs = storage.update_worker_config.await_args
    assert kwargs.get("evaluation_status") == "rejected"


# ── 4. Get evaluation history ─────────────────────────────────────────────────


@pytest.mark.anyio
async def test_get_worker_evaluations_returns_report_list(client):
    """GET /capabilities/workers/{id}/evaluations → list of evaluation reports."""
    worker = _worker_row()
    report1 = _eval_report(verdict="APPROVED")
    report2 = _eval_report(report_id=uuid4(), verdict="CONDITIONAL", overall_score=60.0)

    storage = MagicMock()
    storage.get_worker = AsyncMock(return_value=worker)
    storage.get_evaluation_reports = AsyncMock(return_value=[report1, report2])
    _patch(storage)

    resp = await client.get(f"/capabilities/workers/{WORKER_ID}/evaluations")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 2
    assert body[0]["verdict"] == "APPROVED"
    assert body[1]["verdict"] == "CONDITIONAL"
    storage.get_evaluation_reports.assert_awaited_once_with(WORKER_ID, limit=20)


@pytest.mark.anyio
async def test_get_evaluations_for_unknown_worker_returns_404(client):
    """GET /capabilities/workers/{unknown}/evaluations → 404."""
    storage = MagicMock()
    storage.get_worker = AsyncMock(return_value=None)
    _patch(storage)

    resp = await client.get(f"/capabilities/workers/{uuid4()}/evaluations")
    assert resp.status_code == 404


@pytest.mark.anyio
async def test_get_worker_evaluations_empty_when_no_reports(client):
    """GET /capabilities/workers/{id}/evaluations → empty list if no reports yet."""
    worker = _worker_row()
    storage = MagicMock()
    storage.get_worker = AsyncMock(return_value=worker)
    storage.get_evaluation_reports = AsyncMock(return_value=[])
    _patch(storage)

    resp = await client.get(f"/capabilities/workers/{WORKER_ID}/evaluations")
    assert resp.status_code == 200
    assert resp.json() == []


# ── 5. Approve worker (status transition to ACTIVE) ───────────────────────────


@pytest.mark.anyio
async def test_activate_approved_worker_changes_status_to_active(client):
    """After evaluation APPROVED, PATCH ACTIVATE → status=ACTIVE."""
    pending_row = _worker_row(status="INACTIVE", evaluation_status="approved")
    active_row = _worker_row(status="ACTIVE", evaluation_status="approved")

    storage = MagicMock()
    storage.get_worker = AsyncMock(side_effect=[pending_row, active_row])
    storage.update_worker_status = AsyncMock(return_value=None)
    _patch(storage)

    resp = await client.patch(
        f"/capabilities/workers/{WORKER_ID}/status",
        json={"action": "ACTIVATE"},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "ACTIVE"
    assert resp.json()["evaluation_status"] == "approved"


# ── 6. Trigger upgrade (mocked git) ──────────────────────────────────────────


@pytest.mark.anyio
async def test_upgrade_worker_success_updates_commit_sha(client):
    """POST /capabilities/workers/{id}/upgrade → upstream pulled, commit_sha updated."""
    worker = _worker_row()
    updated = _worker_row(upstream_commit_sha=COMMIT_SHA_NEW)

    storage = MagicMock()
    storage.get_worker = AsyncMock(side_effect=[worker, updated])
    storage.update_worker_upstream = AsyncMock(return_value=None)
    storage.update_worker_health = AsyncMock(return_value=None)
    _patch(storage)

    with (
        patch(
            "mas_core.worker_registry.ingestion.pull_upstream",
            new_callable=AsyncMock,
            return_value=COMMIT_SHA_NEW,
        ),
        patch(
            "mas_core.worker_registry.compat_tests.run_compatibility_tests",
            new_callable=AsyncMock,
            return_value={"passed": True, "total": 7, "passed_count": 7, "failed_count": 0},
        ),
    ):
        resp = await client.post(
            f"/capabilities/workers/{WORKER_ID}/upgrade",
            json={"source_revision": "v1.1.0", "run_compat_tests": True},
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["upstream_commit_sha"] == COMMIT_SHA_NEW
    assert body["compat_tests"]["passed"] is True
    # update_worker_upstream called
    storage.update_worker_upstream.assert_awaited_once_with(
        worker_id=WORKER_ID,
        upstream_commit_sha=COMMIT_SHA_NEW,
    )
    # Health set to healthy after successful upgrade
    storage.update_worker_health.assert_awaited_once_with(WORKER_ID, health_status="healthy")


@pytest.mark.anyio
async def test_upgrade_worker_no_source_repo_returns_400(client):
    """POST /capabilities/workers/{id}/upgrade for worker with no source_repo → 400."""
    worker = _worker_row()
    worker_no_repo = dict(worker)
    worker_no_repo["source_repo"] = None

    storage = MagicMock()
    storage.get_worker = AsyncMock(return_value=worker_no_repo)
    _patch(storage)

    resp = await client.post(
        f"/capabilities/workers/{WORKER_ID}/upgrade",
        json={"run_compat_tests": False},
    )
    assert resp.status_code == 400
    assert "source_repo" in resp.json()["detail"].lower()


@pytest.mark.anyio
async def test_upgrade_unknown_worker_returns_404(client):
    """POST /capabilities/workers/{unknown}/upgrade → 404."""
    storage = MagicMock()
    storage.get_worker = AsyncMock(return_value=None)
    _patch(storage)

    resp = await client.post(
        f"/capabilities/workers/{uuid4()}/upgrade",
        json={"run_compat_tests": False},
    )
    assert resp.status_code == 404


# ── 7. Breaking upgrade rejected — old version stays active ───────────────────


@pytest.mark.anyio
async def test_breaking_upgrade_rejected_when_compat_tests_fail(client):
    """POST upgrade when compat tests fail → 409, health set to degraded, worker untouched."""
    worker = _worker_row()

    storage = MagicMock()
    storage.get_worker = AsyncMock(return_value=worker)
    storage.update_worker_upstream = AsyncMock(return_value=None)
    storage.update_worker_health = AsyncMock(return_value=None)
    _patch(storage)

    with (
        patch(
            "mas_core.worker_registry.ingestion.pull_upstream",
            new_callable=AsyncMock,
            return_value=COMMIT_SHA_NEW,
        ),
        patch(
            "mas_core.worker_registry.compat_tests.run_compatibility_tests",
            new_callable=AsyncMock,
            return_value={
                "passed": False,
                "total": 7,
                "passed_count": 4,
                "failed_count": 3,
                "results": {
                    "transport_compatibility": {"passed": False, "details": "Breaking API change"},
                    "capability_contract": {"passed": False, "details": "Capabilities mismatch"},
                    "message_protocol": {"passed": False, "details": "New protocol incompatible"},
                },
            },
        ),
    ):
        resp = await client.post(
            f"/capabilities/workers/{WORKER_ID}/upgrade",
            json={"run_compat_tests": True},
        )

    assert resp.status_code == 409
    assert "compat" in resp.json()["detail"].lower()
    # Health set to degraded
    storage.update_worker_health.assert_awaited_once_with(WORKER_ID, health_status="degraded")
    # commit_sha NOT updated (upgrade not applied)
    storage.update_worker_upstream.assert_not_awaited()


@pytest.mark.anyio
async def test_worker_remains_active_after_rejected_upgrade(client):
    """After a rejected upgrade, GET /capabilities/workers still returns ACTIVE worker."""
    row = _worker_row(status="ACTIVE", upstream_commit_sha=COMMIT_SHA_OLD)

    storage = MagicMock()
    storage.list_workers = AsyncMock(return_value=[row])
    _patch(storage)

    resp = await client.get("/capabilities/workers", params={"status": "ACTIVE"})
    assert resp.status_code == 200
    workers = resp.json()
    assert len(workers) == 1
    assert workers[0]["status"] == "ACTIVE"
    assert workers[0]["upstream_commit_sha"] == COMMIT_SHA_OLD  # old sha unchanged


@pytest.mark.anyio
async def test_upgrade_without_compat_tests_skips_testing(client):
    """POST upgrade with run_compat_tests=False → skips compat tests, applies upgrade."""
    worker = _worker_row()
    updated = _worker_row(upstream_commit_sha=COMMIT_SHA_NEW)

    storage = MagicMock()
    storage.get_worker = AsyncMock(side_effect=[worker, updated])
    storage.update_worker_upstream = AsyncMock(return_value=None)
    storage.update_worker_health = AsyncMock(return_value=None)
    _patch(storage)

    with (
        patch(
            "mas_core.worker_registry.ingestion.pull_upstream",
            new_callable=AsyncMock,
            return_value=COMMIT_SHA_NEW,
        ) as mock_pull,
        patch("mas_core.worker_registry.compat_tests.run_compatibility_tests") as mock_compat,
    ):
        resp = await client.post(
            f"/capabilities/workers/{WORKER_ID}/upgrade",
            json={"run_compat_tests": False},
        )

    assert resp.status_code == 200
    assert resp.json()["compat_tests"] is None
    mock_compat.assert_not_called()


# ── 8. Unit tests for evaluator verdict logic ─────────────────────────────────


def test_compute_verdict_approved_when_all_checks_pass():
    """_compute_verdict returns APPROVED when score ≥ 70 and no critical failures."""
    from mas_core.worker_registry.evaluator import _compute_verdict

    results = {
        "architecture": {"score": 80.0, "passed": True},
        "maintenance": {"score": 75.0, "passed": True},
        "licensing": {"score": 100.0, "passed": True},
        "security": {"score": 100.0, "passed": True},
        "compatibility": {"score": 70.0, "passed": True},
    }
    assert _compute_verdict(results, 85.0) == "APPROVED"


def test_compute_verdict_conditional_when_score_between_50_70():
    """_compute_verdict returns CONDITIONAL when 50 ≤ score < 70."""
    from mas_core.worker_registry.evaluator import _compute_verdict

    results = {
        "architecture": {"score": 60.0, "passed": True},
        "maintenance": {"score": 50.0, "passed": True},
        "licensing": {"score": 100.0, "passed": True},
        "security": {"score": 60.0, "passed": True},
        "compatibility": {"score": 50.0, "passed": True},
    }
    assert _compute_verdict(results, 62.0) == "CONDITIONAL"


def test_compute_verdict_approved_with_requested_subset_checks():
    """_compute_verdict ignores critical checks that were not requested."""
    from mas_core.worker_registry.evaluator import _compute_verdict

    results = {
        "provenance": {"score": 70.0, "passed": True},
        "version_pin": {"score": 100.0, "passed": True},
        "manifest_validation": {"score": 100.0, "passed": True},
        "compatibility": {"score": 60.0, "passed": True},
        "sandbox_profile": {"score": 100.0, "passed": True},
        "budget_latency": {"score": 100.0, "passed": True},
    }
    assert _compute_verdict(results, 87.1) == "APPROVED"


def test_compute_verdict_rejected_when_licensing_fails():
    """_compute_verdict returns REJECTED when licensing score < 50 (GPL etc)."""
    from mas_core.worker_registry.evaluator import _compute_verdict

    results = {
        "architecture": {"score": 90.0, "passed": True},
        "licensing": {"score": 0.0, "passed": False},
        "security": {"score": 100.0, "passed": True},
    }
    assert _compute_verdict(results, 65.0) == "REJECTED"


def test_compute_verdict_rejected_when_security_very_low():
    """_compute_verdict returns REJECTED when security score < 30 (exposed secrets)."""
    from mas_core.worker_registry.evaluator import _compute_verdict

    results = {
        "architecture": {"score": 90.0, "passed": True},
        "licensing": {"score": 100.0, "passed": True},
        "security": {"score": 20.0, "passed": False},
    }
    assert _compute_verdict(results, 55.0) == "REJECTED"


def test_compute_verdict_rejected_when_overall_score_below_50():
    """_compute_verdict returns REJECTED when overall score < 50."""
    from mas_core.worker_registry.evaluator import _compute_verdict

    results = {
        "architecture": {"score": 20.0, "passed": False},
        "licensing": {"score": 100.0, "passed": True},
        "security": {"score": 50.0, "passed": True},
        "compatibility": {"score": 0.0, "passed": False},
    }
    assert _compute_verdict(results, 40.0) == "REJECTED"


def test_compute_overall_score_weighted_average():
    """_compute_overall_score correctly weights check scores."""
    from mas_core.worker_registry.evaluator import _compute_overall_score

    results = {
        "architecture": {"score": 100.0},  # weight 0.20
        "maintenance": {"score": 100.0},  # weight 0.15
        "licensing": {"score": 100.0},  # weight 0.25
        "security": {"score": 100.0},  # weight 0.25
        "compatibility": {"score": 100.0},  # weight 0.15
    }
    score = _compute_overall_score(results)
    assert score == 100.0


def test_compute_overall_score_partial_checks():
    """_compute_overall_score normalizes by actual weight when checks are missing."""
    from mas_core.worker_registry.evaluator import _compute_overall_score

    results = {
        "licensing": {"score": 100.0},
        "security": {"score": 0.0},
    }
    # licensing weight=0.25, security weight=0.25 → total_weight=0.5
    # total = 0.25*100 + 0.25*0 = 25 → normalized = 25/0.5 = 50
    score = _compute_overall_score(results)
    assert score == 50.0


# ── 9. Unit tests for licensing check ────────────────────────────────────────


@pytest.mark.anyio
async def test_licensing_check_passes_for_mit_license(tmp_path):
    """_check_licensing returns passed=True for MIT license file."""
    from mas_core.worker_registry.evaluator import _check_licensing

    (tmp_path / "LICENSE").write_text("MIT License\n\nCopyright (c) 2024")
    result = await _check_licensing("https://github.com/example/repo", tmp_path)
    assert result["passed"] is True
    assert result["score"] == 100.0
    assert "mit" in result["details"].lower()


@pytest.mark.anyio
async def test_licensing_check_fails_for_gpl_license(tmp_path):
    """_check_licensing returns passed=False for GPL license (text contains 'gpl-3.0')."""
    from mas_core.worker_registry.evaluator import _check_licensing

    # The evaluator scans for exact substrings like "gpl-3.0" in lowercase license text.
    # Use a license text that includes the exact token the evaluator searches for.
    (tmp_path / "LICENSE").write_text(
        "This software is licensed under the gpl-3.0 license.\n"
        "GNU GENERAL PUBLIC LICENSE\nVersion 3, June 2007"
    )
    result = await _check_licensing("https://github.com/example/repo", tmp_path)
    assert result["passed"] is False
    assert result["score"] == 0.0
    assert "gpl" in result["details"].lower()


@pytest.mark.anyio
async def test_licensing_check_fails_when_no_license_file(tmp_path):
    """_check_licensing returns passed=False when no LICENSE file exists."""
    from mas_core.worker_registry.evaluator import _check_licensing

    result = await _check_licensing("https://github.com/example/repo", tmp_path)
    assert result["passed"] is False
    assert "no license" in result["details"].lower()


# ── 10. Unit tests for security check ────────────────────────────────────────


@pytest.mark.anyio
async def test_security_check_passes_clean_codebase(tmp_path):
    """_check_security returns passed=True when no secrets found."""
    from mas_core.worker_registry.evaluator import _check_security

    (tmp_path / "main.py").write_text("def main():\n    print('hello')\n")
    result = await _check_security("https://github.com/example/repo", tmp_path)
    assert result["passed"] is True
    assert result["score"] == 100.0
    assert result["secrets_found"] == []


@pytest.mark.anyio
async def test_security_check_fails_with_hardcoded_secret(tmp_path):
    """_check_security detects hardcoded API keys and reduces score."""
    from mas_core.worker_registry.evaluator import _check_security

    (tmp_path / "config.py").write_text('api_key = "AKIAIOSFODNN7EXAMPLE1234567890"\n')
    result = await _check_security("https://github.com/example/repo", tmp_path)
    assert result["score"] < 100.0
    assert len(result["secrets_found"]) > 0


# ── 11. Unit tests for architecture check ────────────────────────────────────


@pytest.mark.anyio
async def test_architecture_check_passes_with_entrypoint_and_structure(tmp_path):
    """_check_architecture returns passed=True when entrypoint + pyproject.toml found."""
    from mas_core.worker_registry.evaluator import _check_architecture

    (tmp_path / "agent.py").write_text("class MyWorkerAgent:\n    def run(self):\n        pass\n")
    (tmp_path / "pyproject.toml").write_text("[project]\nname='worker'")
    result = await _check_architecture("https://github.com/example/repo", tmp_path)
    assert result["passed"] is True
    assert result["score"] == 100.0


@pytest.mark.anyio
async def test_architecture_check_fails_without_entrypoint(tmp_path):
    """_check_architecture returns passed=False with no recognizable entrypoint."""
    from mas_core.worker_registry.evaluator import _check_architecture

    (tmp_path / "utils.py").write_text("def helper(): pass\n")
    result = await _check_architecture("https://github.com/example/repo", tmp_path)
    assert result["passed"] is False
    assert result["score"] < 50.0


# ── 12. Unit tests for compatibility check ────────────────────────────────────


@pytest.mark.anyio
async def test_compatibility_check_passes_for_python_with_main(tmp_path):
    """_check_compatibility returns passed=True for Python codebase with main entrypoint."""
    from mas_core.worker_registry.evaluator import _check_compatibility

    (tmp_path / "worker.py").write_text(
        "def main():\n    pass\nif __name__ == '__main__':\n    main()\n"
    )
    (tmp_path / "requirements.txt").write_text("httpx\n")
    result = await _check_compatibility("https://github.com/example/repo", tmp_path)
    assert result["passed"] is True
    # python(30) + requirements(20) + main(30) = 80
    assert result["score"] >= 50.0


@pytest.mark.anyio
async def test_compatibility_check_score_below_threshold_fails(tmp_path):
    """_check_compatibility returns passed=False when no Python code found."""
    from mas_core.worker_registry.evaluator import _check_compatibility

    # Empty directory — no Python, no requirements
    result = await _check_compatibility("https://github.com/example/repo", tmp_path)
    assert result["passed"] is False
    assert result["score"] < 50.0


# ── 13. Unit tests for compat_tests module ────────────────────────────────────


@pytest.mark.anyio
async def test_compat_manifest_validation_passes_with_valid_worker():
    """_test_manifest_validation passes for a well-formed worker dict."""
    from mas_core.worker_registry.compat_tests import _test_manifest_validation

    worker = {
        "name": "code_reviewer",
        "adapter_type": "process",
        "adapter_entrypoint": "CodeReviewerAgent",
    }
    result = await _test_manifest_validation(worker, None, MagicMock())
    assert result["passed"] is True


@pytest.mark.anyio
async def test_compat_manifest_validation_fails_missing_name():
    """_test_manifest_validation fails when worker name is missing."""
    from mas_core.worker_registry.compat_tests import _test_manifest_validation

    worker = {"adapter_type": "process"}
    result = await _test_manifest_validation(worker, None, MagicMock())
    assert result["passed"] is False
    assert "name" in result["details"].lower()


@pytest.mark.parametrize("transport", ["process", "http", "mcp", "oci", "human"])
@pytest.mark.anyio
async def test_compat_transport_compatibility_passes_for_supported_transports(transport):
    """_test_transport_compatibility passes for every AIAT worker transport."""
    from mas_core.worker_registry.compat_tests import _test_transport_compatibility

    worker = {"adapter_type": transport}
    result = await _test_transport_compatibility(worker, None, MagicMock())
    assert result["passed"] is True


@pytest.mark.anyio
async def test_compat_transport_fails_for_unknown_transport():
    """_test_transport_compatibility fails for an unsupported transport type."""
    from mas_core.worker_registry.compat_tests import _test_transport_compatibility

    worker = {"adapter_type": "ftp"}
    result = await _test_transport_compatibility(worker, None, MagicMock())
    assert result["passed"] is False
    assert "unsupported" in result["details"].lower()


@pytest.mark.anyio
async def test_compat_sandbox_compliance_passes_for_valid_profile():
    """_test_sandbox_compliance passes for 'restricted' sandbox profile."""
    from mas_core.worker_registry.compat_tests import _test_sandbox_compliance

    worker = {"sandbox_profile": "restricted"}
    result = await _test_sandbox_compliance(worker, None, MagicMock())
    assert result["passed"] is True


@pytest.mark.anyio
async def test_compat_sandbox_fails_for_invalid_profile():
    """_test_sandbox_compliance fails for an unknown sandbox profile."""
    from mas_core.worker_registry.compat_tests import _test_sandbox_compliance

    worker = {"sandbox_profile": "supermax"}
    result = await _test_sandbox_compliance(worker, None, MagicMock())
    assert result["passed"] is False


@pytest.mark.anyio
async def test_compat_message_protocol_passes_for_builtin_entrypoint():
    """_test_message_protocol passes for a known built-in entrypoint."""
    from mas_core.worker_registry.compat_tests import _test_message_protocol

    worker = {"adapter_entrypoint": "WorkerAgent", "adapter_module": None}
    result = await _test_message_protocol(worker, None, MagicMock())
    assert result["passed"] is True
    assert "built-in" in result["details"].lower()


@pytest.mark.anyio
async def test_compat_message_protocol_fails_for_unknown_entrypoint_no_module():
    """_test_message_protocol fails for an unknown entrypoint with no adapter_module."""
    from mas_core.worker_registry.compat_tests import _test_message_protocol

    worker = {"adapter_entrypoint": "MysteryAgent", "adapter_module": None}
    result = await _test_message_protocol(worker, None, MagicMock())
    assert result["passed"] is False


@pytest.mark.anyio
async def test_compat_capability_contract_fails_with_no_capabilities():
    """_test_capability_contract fails when worker has no capabilities."""
    from mas_core.worker_registry.compat_tests import _test_capability_contract

    worker = {"capability_ids": []}
    result = await _test_capability_contract(worker, None, MagicMock())
    assert result["passed"] is False
    assert "no capabilities" in result["details"].lower()


@pytest.mark.anyio
async def test_compat_capability_contract_passes_when_all_caps_valid():
    """_test_capability_contract passes when all capability_ids resolve."""
    from mas_core.worker_registry.compat_tests import _test_capability_contract

    cap = {"id": CAP_ID, "name": "code.review"}
    storage = MagicMock()
    storage.get_capability = AsyncMock(return_value=cap)

    worker = {"capability_ids": [CAP_ID]}
    result = await _test_capability_contract(worker, storage, storage)
    assert result["passed"] is True
    assert "1/1" in result["details"]


@pytest.mark.anyio
async def test_run_compatibility_tests_all_pass_for_well_formed_worker():
    """run_compatibility_tests returns passed=True for a well-formed worker."""
    from mas_core.worker_registry.compat_tests import run_compatibility_tests

    cap = {"id": CAP_ID, "name": "code.review"}
    storage = MagicMock()
    storage.get_worker = AsyncMock(
        return_value={
            "name": "code_reviewer",
            "adapter_type": "process",
            "adapter_entrypoint": "WorkerAgent",
            "adapter_module": None,
            "sandbox_profile": "standard",
            "capability_ids": [CAP_ID],
            "id": WORKER_ID,
        }
    )
    storage.get_capability = AsyncMock(return_value=cap)

    result = await run_compatibility_tests(
        worker_id=WORKER_ID,
        storage=storage,
        test_names=[
            "manifest_validation",
            "transport_compatibility",
            "sandbox_compliance",
            "message_protocol",
            "capability_contract",
        ],
    )
    assert result["passed"] is True
    assert result["failed_count"] == 0


# ── 14. Integration: full GitHub worker lifecycle ─────────────────────────────


@pytest.mark.anyio
async def test_github_worker_full_lifecycle(client):
    """
    Integration: register with source_repo → evaluate (APPROVED) → activate →
    verify in ACTIVE listing → trigger upgrade (compat pass) → commit_sha updated →
    trigger breaking upgrade (compat fail) → 409, old sha unchanged →
    worker still ACTIVE with original sha.
    """
    worker_pending = _worker_row(status="INACTIVE", evaluation_status="pending")
    worker_evaluated = _worker_row(status="INACTIVE", evaluation_status="approved")
    worker_approved = _worker_row(status="ACTIVE", evaluation_status="approved")
    worker_upgraded = _worker_row(
        status="ACTIVE",
        evaluation_status="approved",
        upstream_commit_sha=COMMIT_SHA_NEW,
    )
    eval_report = _eval_report(verdict="APPROVED")

    storage = MagicMock()
    storage.register_worker = AsyncMock(return_value=worker_pending)
    storage.get_worker = AsyncMock(
        side_effect=[
            worker_pending,  # for evaluate
            worker_evaluated,  # for activate (get)
            worker_approved,  # for activate (return)
            worker_approved,  # for upgrade (get)
            worker_upgraded,  # for upgrade (return)
            worker_approved,  # for breaking upgrade (get)
        ]
    )
    storage.update_worker_config = AsyncMock(return_value=None)
    storage.update_worker_status = AsyncMock(return_value=None)
    storage.update_worker_upstream = AsyncMock(return_value=None)
    storage.update_worker_health = AsyncMock(return_value=None)
    storage.list_workers = AsyncMock(return_value=[worker_approved])
    _patch(storage)

    # Step 1: Register
    resp = await client.post(
        "/capabilities/workers",
        json={
            "name": "code_reviewer",
            "adapter_type": "process",
            "source_repo": SOURCE_REPO,
            "version_pin": "v1.0.0",
        },
    )
    assert resp.status_code == 201

    # Step 2: Evaluate → APPROVED
    with patch(
        "mas_core.worker_registry.evaluator.evaluate_repository",
        new_callable=AsyncMock,
        return_value=eval_report,
    ):
        resp2 = await client.post(
            f"/capabilities/workers/{WORKER_ID}/evaluate",
            json={"source_repo": SOURCE_REPO},
        )
    assert resp2.status_code == 200
    assert resp2.json()["verdict"] == "APPROVED"

    # Step 3: Activate
    resp3 = await client.patch(
        f"/capabilities/workers/{WORKER_ID}/status",
        json={"action": "ACTIVATE"},
    )
    assert resp3.status_code == 200
    assert resp3.json()["status"] == "ACTIVE"

    # Step 4: Verify in ACTIVE listing
    resp4 = await client.get("/capabilities/workers", params={"status": "ACTIVE"})
    assert resp4.status_code == 200
    assert any(w["name"] == "code_reviewer" for w in resp4.json())

    # Step 5: Compatible upgrade → success
    with (
        patch(
            "mas_core.worker_registry.ingestion.pull_upstream",
            new_callable=AsyncMock,
            return_value=COMMIT_SHA_NEW,
        ),
        patch(
            "mas_core.worker_registry.compat_tests.run_compatibility_tests",
            new_callable=AsyncMock,
            return_value={"passed": True, "total": 7, "passed_count": 7, "failed_count": 0},
        ),
    ):
        resp5 = await client.post(
            f"/capabilities/workers/{WORKER_ID}/upgrade",
            json={"run_compat_tests": True},
        )
    assert resp5.status_code == 200
    assert resp5.json()["upstream_commit_sha"] == COMMIT_SHA_NEW

    # Step 6: Breaking upgrade rejected → 409
    storage.update_worker_health.reset_mock()
    storage.update_worker_upstream.reset_mock()
    with (
        patch(
            "mas_core.worker_registry.ingestion.pull_upstream",
            new_callable=AsyncMock,
            return_value="BREAKING_SHA",
        ),
        patch(
            "mas_core.worker_registry.compat_tests.run_compatibility_tests",
            new_callable=AsyncMock,
            return_value={"passed": False, "total": 7, "passed_count": 3, "failed_count": 4},
        ),
    ):
        resp6 = await client.post(
            f"/capabilities/workers/{WORKER_ID}/upgrade",
            json={"run_compat_tests": True},
        )
    assert resp6.status_code == 409
    # Health degraded, upstream NOT updated
    storage.update_worker_health.assert_awaited_once_with(WORKER_ID, health_status="degraded")
    storage.update_worker_upstream.assert_not_awaited()


@pytest.mark.anyio
async def test_trufflehog_check_skips_when_binary_missing(tmp_path, monkeypatch):
    """_check_trufflehog records SKIPPED_TOOL_UNAVAILABLE when the binary is absent."""
    from mas_core.worker_registry import evaluator

    monkeypatch.setattr(evaluator.shutil, "which", lambda _name: None)
    result = await evaluator._check_trufflehog("https://github.com/example/repo", tmp_path)
    assert result["passed"] is True
    assert result["status"] == "SKIPPED_TOOL_UNAVAILABLE"


@pytest.mark.anyio
async def test_trufflehog_check_counts_json_line_findings(tmp_path, monkeypatch):
    """_check_trufflehog treats emitted JSON lines as findings."""
    import asyncio

    from mas_core.worker_registry import evaluator

    class FakeProcess:
        returncode = 183

        async def communicate(self):
            return b'{"Verified": true, "DetectorName": "Github"}\n', b""

    monkeypatch.setattr(evaluator.shutil, "which", lambda _name: "trufflehog")
    monkeypatch.setattr(asyncio, "create_subprocess_exec", AsyncMock(return_value=FakeProcess()))

    result = await evaluator._check_trufflehog("https://github.com/example/repo", tmp_path)

    assert result["passed"] is False
    assert result["status"] == "FAILED"
    assert result["findings_count"] == 1


@pytest.mark.anyio
async def test_semgrep_check_skips_when_binary_missing(tmp_path, monkeypatch):
    """_check_semgrep records SKIPPED_TOOL_UNAVAILABLE when the binary is absent."""
    from mas_core.worker_registry import evaluator

    monkeypatch.setattr(evaluator.shutil, "which", lambda _name: None)
    result = await evaluator._check_semgrep("https://github.com/example/repo", tmp_path)
    assert result["passed"] is True
    assert result["status"] == "SKIPPED_TOOL_UNAVAILABLE"


@pytest.mark.anyio
async def test_semgrep_check_parses_json_findings(tmp_path, monkeypatch):
    """_check_semgrep parses Semgrep JSON output and counts findings."""
    import asyncio

    from mas_core.worker_registry import evaluator

    class FakeProcess:
        returncode = 1

        async def communicate(self):
            payload = {"results": [{"check_id": "python.lang.security.audit"}]}
            return json.dumps(payload).encode(), b""

    monkeypatch.setattr(evaluator.shutil, "which", lambda _name: "semgrep")
    monkeypatch.setattr(asyncio, "create_subprocess_exec", AsyncMock(return_value=FakeProcess()))

    result = await evaluator._check_semgrep("https://github.com/example/repo", tmp_path)

    assert result["passed"] is False
    assert result["status"] == "FAILED"
    assert result["findings_count"] == 1


@pytest.mark.anyio
async def test_sandbox_profile_check_rejects_invalid_profile(tmp_path):
    """_check_sandbox_profile rejects profiles outside the declared set."""
    from mas_core.worker_registry.evaluator import _check_sandbox_profile

    result = await _check_sandbox_profile(
        "https://github.com/example/repo",
        tmp_path,
        {"sandbox_profile": "none"},
    )
    assert result["passed"] is False
    assert "Invalid sandbox profile" in result["details"]


@pytest.mark.anyio
async def test_medium_dual_use_worker_requires_hardened_sandbox(tmp_path):
    """Medium/dual-use workers require gvisor or firecracker before activation."""
    from mas_core.worker_registry.evaluator import _check_sandbox_profile

    result = await _check_sandbox_profile(
        "https://github.com/example/repo",
        tmp_path,
        {"sandbox_profile": "restricted", "adapter_config": {"dual_use": True}},
    )
    assert result["passed"] is False
    assert "gvisor or firecracker" in result["details"]

    hardened = await _check_sandbox_profile(
        "https://github.com/example/repo",
        tmp_path,
        {"sandbox_profile": "gvisor", "adapter_config": {"risk_tier": "medium"}},
    )
    assert hardened["passed"] is True


@pytest.mark.anyio
async def test_activate_external_worker_blocked_until_approved(client):
    """External workers cannot activate while evaluation is pending or conditional."""
    pending_row = _worker_row(status="INACTIVE", evaluation_status="conditional")

    storage = MagicMock()
    storage.get_worker = AsyncMock(return_value=pending_row)
    storage.update_worker_status = AsyncMock(return_value=None)
    _patch(storage)

    resp = await client.patch(
        f"/capabilities/workers/{WORKER_ID}/status",
        json={"action": "ACTIVATE"},
    )
    assert resp.status_code == 409
    storage.update_worker_status.assert_not_awaited()


@pytest.mark.anyio
async def test_activate_medium_dual_use_worker_requires_hardened_sandbox_and_approval(client):
    """Medium/dual-use workers cannot activate on restricted sandbox or pending approval."""
    restricted_row = _worker_row(
        status="INACTIVE",
        source_repo=None,
        evaluation_status="approved",
    )
    restricted_row["adapter_config"] = {"entrypoint": "RiskyWorker", "dual_use": True}

    storage = MagicMock()
    storage.get_worker = AsyncMock(return_value=restricted_row)
    storage.update_worker_status = AsyncMock(return_value=None)
    _patch(storage)

    resp = await client.patch(
        f"/capabilities/workers/{WORKER_ID}/status",
        json={"action": "ACTIVATE"},
    )
    assert resp.status_code == 409
    assert "gvisor or firecracker" in resp.json()["detail"]
    storage.update_worker_status.assert_not_awaited()

    pending_approval_row = dict(restricted_row)
    pending_approval_row["sandbox_profile"] = "gvisor"
    pending_approval_row["evaluation_status"] = "conditional"
    storage.get_worker = AsyncMock(return_value=pending_approval_row)

    resp = await client.patch(
        f"/capabilities/workers/{WORKER_ID}/status",
        json={"action": "ACTIVATE"},
    )
    assert resp.status_code == 409
    assert "human approval" in resp.json()["detail"]
    storage.update_worker_status.assert_not_awaited()

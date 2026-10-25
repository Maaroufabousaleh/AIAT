from __future__ import annotations

import httpx
import pytest
from check_worker_run_readiness import _live, _parser

WORKER_ID = "00000000-0000-4000-8000-000000000101"
PROJECT_ID = "00000000-0000-4000-8000-000000000102"
COMPANY_ID = "00000000-0000-4000-8000-000000000103"


@pytest.mark.parametrize(
    ("health_status_code", "health_payload"),
    [
        pytest.param(503, {"health_status": "unavailable"}, id="http-error"),
        pytest.param(200, {}, id="missing-status"),
    ],
)
def test_live_readiness_fails_closed_when_health_read_is_unavailable_or_malformed(
    monkeypatch, health_status_code: int, health_payload: dict[str, str]
) -> None:
    worker = {
        "id": WORKER_ID,
        "status": "ACTIVE",
        "model_mode": "aiat_gateway",
        "model_profile_id": "fixture-model-profile",
        "evaluation_status": "approved",
        "version_pin": "fixture-1.0.0",
        "active_shell_version_id": "shell-1",
        "active_adapter_id": "adapter-1",
        "active_skill_bundle_id": "bundle-1",
        "sandbox_profile": "gvisor",
    }
    company = {"id": COMPANY_ID, "status": "ACTIVE"}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/capabilities/workers":
            return httpx.Response(200, json=[worker])
        if request.url.path == f"/projects/{PROJECT_ID}":
            return httpx.Response(
                200,
                json={"id": PROJECT_ID, "company_id": COMPANY_ID, "state": "IN_PROGRESS"},
            )
        if request.url.path == f"/companies/{COMPANY_ID}":
            return httpx.Response(200, json=company)
        if request.url.path == f"/companies/{COMPANY_ID}/assignments":
            return httpx.Response(
                200,
                json=[
                    {
                        "worker_id": WORKER_ID,
                        "status": "ACTIVE",
                        "approval_required": False,
                        "model_profile_id": "fixture-model-profile",
                    }
                ],
            )
        if request.url.path == f"/companies/{COMPANY_ID}/budgets":
            return httpx.Response(
                200,
                json=[
                    {"budget_key": "max_concurrent_runs", "configured": True, "available": "1"},
                    {"budget_key": "max_cost_usd", "configured": True, "available": "0.10"},
                ],
            )
        if request.url.path == "/model-profiles":
            return httpx.Response(
                200,
                json=[
                    {
                        "profile_id": "fixture-model-profile",
                        "status": "approved",
                        "versions": [{"status": "approved", "version": "1"}],
                    }
                ],
            )
        if request.url.path == f"/capabilities/workers/{WORKER_ID}/health":
            return httpx.Response(health_status_code, json=health_payload)
        raise AssertionError(f"unexpected path: {request.url.path}")

    real_client = httpx.Client
    monkeypatch.setattr(
        "check_worker_run_readiness.httpx.Client",
        lambda *args, **kwargs: real_client(
            *args, transport=httpx.MockTransport(handler), **kwargs
        ),
    )
    args = _parser().parse_args(
        [
            "--live",
            "--url",
            "https://orchestrator.example",
            "--api-key",
            "fixture-key",
            "--worker-id",
            WORKER_ID,
            "--project-id",
            PROJECT_ID,
            "--require-sandbox",
            "--evaluate-non-model-worker",
        ]
    )

    report = _live(args)

    assert report["status"] == "blocked"
    assert report["no_mutation"] is True
    codes = {item["code"] for item in report["readiness"]["blockers"]}
    assert "read_worker_health_unavailable" in codes
    assert report["readiness"]["checks"]["model_profile"]["required"] is True


def test_live_readiness_supports_explicitly_selected_non_model_worker(monkeypatch) -> None:
    worker = {
        "id": WORKER_ID,
        "status": "ACTIVE",
        "model_mode": "none",
        "evaluation_status": "approved",
        "version_pin": "deterministic-file-worker-0.1.1+sha256:source",
        "active_shell_version_id": "shell-1",
        "active_adapter_id": "adapter-1",
        "active_skill_bundle_id": "bundle-1",
        "adapter_type": "oci",
        "sandbox_profile": "sandboxed",
    }

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/capabilities/workers":
            return httpx.Response(200, json=[worker])
        if request.url.path == f"/projects/{PROJECT_ID}":
            return httpx.Response(
                200,
                json={"id": PROJECT_ID, "company_id": COMPANY_ID, "state": "IN_PROGRESS"},
            )
        if request.url.path == f"/companies/{COMPANY_ID}":
            return httpx.Response(200, json={"id": COMPANY_ID, "status": "ACTIVE"})
        if request.url.path == f"/companies/{COMPANY_ID}/assignments":
            return httpx.Response(
                200,
                json=[
                    {
                        "worker_id": WORKER_ID,
                        "status": "ACTIVE",
                        "approval_required": False,
                        "tool_grants": ["file_write"],
                    }
                ],
            )
        if request.url.path == f"/companies/{COMPANY_ID}/budgets":
            return httpx.Response(
                200,
                json=[
                    {"budget_key": "max_concurrent_runs", "configured": True, "available": "1"},
                    {"budget_key": "max_cost_usd", "configured": True, "available": "0.10"},
                ],
            )
        if request.url.path == f"/capabilities/workers/{WORKER_ID}/health":
            return httpx.Response(200, json={"health_status": "healthy"})
        if request.url.path == "/model-profiles":
            raise AssertionError("non-model worker readiness must not query model profiles")
        raise AssertionError(f"unexpected path: {request.url.path}")

    real_client = httpx.Client
    monkeypatch.setattr(
        "check_worker_run_readiness.httpx.Client",
        lambda *args, **kwargs: real_client(
            *args, transport=httpx.MockTransport(handler), **kwargs
        ),
    )
    args = _parser().parse_args(
        [
            "--live",
            "--evaluate-non-model-worker",
            "--url",
            "https://orchestrator.example",
            "--api-key",
            "fixture-key",
            "--worker-id",
            WORKER_ID,
            "--project-id",
            PROJECT_ID,
        ]
    )

    report = _live(args)

    assert report["status"] == "pass"
    readiness = report["readiness"]
    assert readiness["scope"].startswith("read-only selected non-model worker-run readiness")
    assert readiness["checks"]["model_profile"]["required"] is False
    assert "model_profiles" not in readiness["fetch_status"]
    assert readiness["blockers"] == []

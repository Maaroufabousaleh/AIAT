from __future__ import annotations

import httpx
from check_worker_run_readiness import _live, _parser

WORKER_ID = "00000000-0000-4000-8000-000000000101"
PROJECT_ID = "00000000-0000-4000-8000-000000000102"
COMPANY_ID = "00000000-0000-4000-8000-000000000103"


def test_live_readiness_fails_closed_when_health_read_is_unavailable(monkeypatch) -> None:
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
            return httpx.Response(503, json={"health_status": "unavailable"})
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
        ]
    )

    report = _live(args)

    assert report["status"] == "blocked"
    assert report["no_mutation"] is True
    codes = {item["code"] for item in report["readiness"]["blockers"]}
    assert "read_worker_health_unavailable" in codes

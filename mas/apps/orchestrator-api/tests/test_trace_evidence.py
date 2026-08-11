from __future__ import annotations

import pytest


class _TraceStorage:
    async def list_task_logs(self, *, trace_id: str, limit: int) -> list[dict]:
        assert trace_id == "trace-123"
        return [
            {
                "task_id": "task-1",
                "agent_id": "tester",
                "team_id": "dept_qa",
                "status": "SUCCEEDED",
                "input": {"project_id": "project-1", "secret": "do-not-return"},
                "created_at": "2026-08-10T00:00:00+00:00",
            }
        ]

    async def list_project_usage_events_by_trace(self, trace_id: str, *, limit: int) -> list[dict]:
        assert trace_id == "trace-123"
        return [
            {
                "id": "usage-1",
                "project_id": "project-1",
                "event_type": "tool",
                "tool_name": "clock.now",
                "status": "success",
                "details": {"secret": "do-not-return"},
                "occurred_at": "2026-08-10T00:00:01+00:00",
            }
        ]

    async def list_worker_run_transitions_by_correlation(
        self,
        correlation_id: str,
        *,
        limit: int,
    ) -> list[dict]:
        assert correlation_id == "trace-123"
        return [
            {
                "id": "transition-1",
                "run_id": "run-1",
                "to_state": "RUNNING",
                "metadata": {"secret": "do-not-return"},
                "created_at": "2026-08-10T00:00:02+00:00",
            }
        ]

    async def list_worker_usage_records_by_trace(self, trace_id: str, *, limit: int) -> list[dict]:
        assert trace_id == "trace-123"
        return [
            {
                "id": "worker-usage-1",
                "run_id": "run-1",
                "provider_id": "omniroute",
                "exact_model_id": "model-1",
                "prompt_tokens": 10,
                "completion_tokens": 5,
                "resource_json": {"secret": "do-not-return"},
                "created_at": "2026-08-10T00:00:03+00:00",
            }
        ]

    async def list_worker_artifacts_by_trace(self, trace_id: str, *, limit: int) -> list[dict]:
        assert trace_id == "trace-123"
        return [
            {
                "id": "artifact-1",
                "run_id": "run-1",
                "artifact_id": 42,
                "kind": "report",
                "sha256": "a" * 64,
                "size_bytes": 128,
                "metadata": {"secret": "do-not-return"},
                "created_at": "2026-08-10T00:00:03+00:00",
            }
        ]

    async def list_pm_inbox_events_by_correlation(self, correlation_id: str, *, limit: int) -> list[dict]:
        assert correlation_id == "trace-123"
        return [
            {
                "id": "pm-event-1",
                "connection_id": "connection-1",
                "event_type": "issue.updated",
                "status": "PROCESSED",
                "payload": {"secret": "do-not-return"},
                "received_at": "2026-08-10T00:00:04+00:00",
            }
        ]

    async def list_integration_evidence_by_trace(self, trace_id: str, *, limit: int) -> list[dict]:
        assert trace_id == "trace-123"
        return [
            {
                "id": "integration-evidence-1",
                "connection_id": "connection-1",
                "project_id": "project-1",
                "evidence_type": "pull_request.updated",
                "span_id": "span-integration-1",
                "created_at": "2026-08-10T00:00:05+00:00",
                "payload": {"secret": "do-not-return"},
            }
        ]

    async def list_api_request_observations(self, *, trace_id: str, limit: int) -> list[dict]:
        assert trace_id == "trace-123"
        return [
            {
                "id": "api-1",
                "method": "GET",
                "route": "/health",
                "status_code": 200,
                "outcome": "success",
                "duration_ms": 2,
                "occurred_at": "2026-08-10T00:00:00+00:00",
            }
        ]

    async def list_native_trace_spans_by_trace(self, trace_id: str, *, limit: int) -> list[dict]:
        assert trace_id == "trace-123"
        return [
            {
                "id": "native-1",
                "trace_id": "trace-123",
                "span_id": "native-span-1",
                "source_kind": "transport",
                "operation": "/health",
                "service": "orchestrator_api",
                "status": "success",
                "sampled": True,
                "started_at": "2026-08-10T00:00:00+00:00",
                "duration_ms": 2,
                "attributes_json": {"secret": "do-not-return"},
            }
        ]

    async def get_company_manifest(self, company_id):
        return {
            "manifest_json": {
                "retention": {
                    "trace_days": 30,
                    "trace_sample_rate": 0.5,
                    "terminal_mode": "archive",
                }
            }
        }


@pytest.mark.anyio
async def test_operator_trace_evidence_is_secret_safe(client):
    from orchestrator_api.main import app

    previous = app.state.storage
    app.state.storage = _TraceStorage()
    try:
        response = await client.get(
            "/observability/traces/trace-123",
            headers={"X-API-Key": "test-operator-key"},
        )
    finally:
        app.state.storage = previous

    assert response.status_code == 200
    payload = response.json()
    assert payload["schema_version"] == "aiat.trace-evidence.v1"
    assert payload["status"] == "observed"
    assert payload["retention"]["retention_days"] == 30
    assert payload["retention"]["sample_rate"] == 0.5
    assert payload["source_counts"]["worker_usage_records"] == 1
    assert payload["source_counts"]["worker_artifacts"] == 1
    assert payload["source_counts"]["pm_inbox_events"] == 1
    assert payload["source_counts"]["integration_evidence"] == 1
    assert payload["source_counts"]["api_requests"] == 1
    assert payload["source_counts"]["native_spans"] == 1
    assert "do-not-return" not in response.text
    assert payload["notices"][0]["code"] == "PARTIAL_TRACE_SOURCES"


@pytest.mark.anyio
async def test_operator_trace_evidence_projects_safe_identity_mail_span(client, monkeypatch):
    from orchestrator_api import main
    from orchestrator_api.main import app

    class _IdentityProjection:
        async def list_mail_delivery_observations(self, *, trace_id: str, limit: int):
            assert trace_id == "trace-123"
            assert limit == 100
            return [
                {
                    "id": "mail-attempt-1",
                    "status": "success",
                    "occurred_at": "2026-08-10T00:00:06+00:00",
                    "source": "identity_outbound_delivery_attempts",
                    "trace_id": trace_id,
                    "span_id": "mail-span-1",
                }
            ]

    monkeypatch.setenv("IDENTITY_SERVICE_URL", "https://identity.example")
    monkeypatch.setenv("AIAT_IDENTITY_CLIENT_PRIVATE_KEY", "configured-for-test")
    monkeypatch.setattr(main, "_identity_client", lambda: _IdentityProjection())
    previous = app.state.storage
    app.state.storage = _TraceStorage()
    try:
        response = await client.get(
            "/observability/traces/trace-123",
            headers={"X-API-Key": "test-operator-key"},
        )
    finally:
        app.state.storage = previous

    assert response.status_code == 200
    payload = response.json()
    assert payload["source_counts"]["native_spans"] == 2
    mail = [item for item in payload["items"] if item["source"] == "native_spans" and item["operation"] == "mail.delivery_attempt"]
    assert len(mail) == 1
    assert {
        key: mail[0][key]
        for key in (
            "id", "source", "kind", "status", "span_id", "operation",
            "service", "occurred_at", "sampled",
        )
    } == {
        "id": "mail-attempt-1",
        "source": "native_spans",
        "kind": "native_span",
        "status": "success",
        "span_id": "mail-span-1",
        "operation": "mail.delivery_attempt",
        "service": "identity_outbound_delivery_attempts",
        "occurred_at": "2026-08-10T00:00:06+00:00",
        "sampled": True,
    }
    assert mail[0]["duration_ms"] == 0


@pytest.mark.anyio
async def test_trace_evidence_requires_operator_and_rejects_invalid_id(client):
    from orchestrator_api.main import app

    previous = app.state.storage
    app.state.storage = _TraceStorage()
    try:
        denied = await client.get(
            "/observability/traces/trace-123",
            headers={"X-API-Key": "test-mas-key"},
        )
        invalid = await client.get(
            "/observability/traces/not%20safe",
            headers={"X-API-Key": "test-operator-key"},
        )
    finally:
        app.state.storage = previous

    assert denied.status_code == 403
    assert invalid.status_code == 422

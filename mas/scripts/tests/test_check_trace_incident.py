from __future__ import annotations

import json

import httpx
from check_trace_incident import _fixture, _live


def test_fixture_reports_attention_as_observed_incident_not_checker_failure() -> None:
    report = _fixture()

    assert report["status"] == "pass"
    assert report["incident_status"] == "attention"
    assert report["severity"] == "critical"
    assert report["finding_count"] == 3
    assert report["fixture_expectation"]["severity"] == "critical"


def test_live_incident_summary_is_bounded_and_redacted(monkeypatch) -> None:
    payload = {
        "schema_version": "aiat.trace-evidence.v1",
        "trace_id": "live-trace-001",
        "status": "observed",
        "item_count": 1,
        "source_counts": {"api_requests": 1},
        "project_ids": [],
        "coverage": {"api_requests": "observed"},
        "notices": [],
        "items": [
            {
                "id": "api-500",
                "source": "api_requests",
                "kind": "api_request",
                "status": "error",
                "status_code": 500,
                "route": "/secret",
                "occurred_at": "2026-08-17T00:00:00+00:00",
                "request_method": "GET",
            }
        ],
    }

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/observability/traces/live-trace-001"
        assert request.url.params["limit"] == "100"
        return httpx.Response(200, json=payload)

    monkeypatch.setattr(httpx, "get", lambda *args, **kwargs: httpx.Client(transport=httpx.MockTransport(handler)).get(*args, **kwargs))
    report = _live(
        url="https://orchestrator.example",
        api_key="secret-key",
        trace_id="live-trace-001",
        limit=100,
        timeout=5,
    )

    assert report["status"] == "observed"
    assert report["incident_status"] == "attention"
    assert report["severity"] == "critical"
    assert report["finding_refs"] == ["api-500"]
    assert "secret-key" not in json.dumps(report)
    assert "/secret" not in json.dumps(report)


def test_live_incident_checker_fails_closed_for_partial_configuration() -> None:
    report = _live(url="https://orchestrator.example", api_key="", trace_id=None, limit=100, timeout=5)

    assert report["status"] == "blocked"
    assert "trace ID" in report["reason"]

from __future__ import annotations

import json

import httpx

from check_trace_retention import _live, build_report


def test_fixture_retention_check_is_non_mutating() -> None:
    report = build_report()

    assert report["status"] == "pass"
    assert report["mutation_performed"] is False
    assert report["licence_metadata_is_gate"] is False


def test_live_retention_summary_is_bounded_and_non_mutating(monkeypatch) -> None:
    payload = {
        "schema_version": "aiat.trace-retention-plan.v1",
        "mode": "read-only-plan",
        "trace_id": "live-trace-001",
        "counts": {"retain": 3, "archive": 2, "delete": 1, "invalid": 0},
        "policy": {
            "retention_days": 30,
            "sample_rate": 0.5,
            "terminal_mode": "delete",
            "source": "company_manifest",
        },
        "notices": ["bounded"],
        "mutation_performed": False,
        "candidates": [{"record_id": "must-not-be-copied"}],
    }

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/observability/retention/plan"
        assert request.url.params["limit"] == "100"
        assert request.url.params["trace_id"] == "live-trace-001"
        return httpx.Response(200, json=payload)

    monkeypatch.setattr(
        httpx,
        "get",
        lambda *args, **kwargs: httpx.Client(transport=httpx.MockTransport(handler)).get(*args, **kwargs),
    )
    report = _live(
        url="https://orchestrator.example",
        api_key="secret-key",
        trace_id="live-trace-001",
        limit=100,
        timeout=5,
    )

    assert report["status"] == "observed"
    assert report["counts"] == {"retain": 3, "archive": 2, "delete": 1, "invalid": 0}
    assert report["mutation_performed"] is False
    assert "secret-key" not in json.dumps(report)
    assert "must-not-be-copied" not in json.dumps(report)


def test_live_retention_checker_fails_closed_for_invalid_plan() -> None:
    report = _live(
        url="https://orchestrator.example",
        api_key="",
        trace_id="not safe",
        limit=100,
        timeout=5,
    )

    assert report["status"] == "blocked"
    assert "trace ID" in report["reason"]

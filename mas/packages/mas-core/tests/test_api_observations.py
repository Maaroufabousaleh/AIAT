from __future__ import annotations

from datetime import UTC, datetime

from mas_core.observability.api_observations import (
    API_OBSERVATION_SCHEMA,
    build_api_observation,
    normalize_api_route,
)


def test_api_route_normalization_removes_ids_and_queries() -> None:
    assert normalize_api_route(
        "/projects/123456/tasks/550e8400-e29b-41d4-a716-446655440000?secret=never"
    ) == "/projects/:id/tasks/:id"
    assert normalize_api_route("/projects/{project_id}/tasks/{task_id}") == "/projects/:param/tasks/:param"


def test_api_observation_is_scalar_bounded_and_trace_safe() -> None:
    row = build_api_observation(
        method="POST",
        path="/projects/42?token=never-return",
        status_code="not-a-status",
        duration_ms=10**20,
        trace_id="unsafe trace value",
        principal="operator",
        dashboard_section="governance",
        occurred_at=datetime(2026, 8, 10, tzinfo=UTC),
    )

    assert row["route"] == "/projects/:id"
    assert row["status_code"] == 500
    assert row["outcome"] == "failure"
    assert row["duration_ms"] == 86_400_000.0
    assert row["trace_id"] is None
    assert row["occurred_at"].tzinfo == UTC
    assert API_OBSERVATION_SCHEMA == "aiat.api-observation.v1"
    assert "never-return" not in str(row)
    assert not {"body", "headers", "query", "exception", "error"}.intersection(row)

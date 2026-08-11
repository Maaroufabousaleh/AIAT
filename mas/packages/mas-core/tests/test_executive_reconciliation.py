"""Deterministic executive model, delivery, usage, and budget reconciliation."""

from mas_core.observability import (
    EXECUTIVE_RECONCILIATION_SCHEMA,
    EXECUTIVE_VIEWS_SCHEMA,
    build_executive_reconciliation,
)


def test_executive_reconciliation_aggregates_durable_sources_and_findings():
    report = build_executive_reconciliation(
        projects=[
            {"id": "p2", "state": "COMPLETED"},
            {"id": "p1", "state": "IN_PROGRESS"},
        ],
        project_usage={
            "p1": {
                "available": True,
                "llm_calls": 2,
                "tool_calls": 1,
                "failed_calls": 1,
                "prompt_tokens": 10,
                "completion_tokens": 5,
                "total_cost_usd": 1.25,
            },
            "p2": {"available": False},
        },
        worker_runs=[
            {"id": "r2", "state": "SUCCEEDED"},
            {"id": "r1", "state": "RUNNING"},
        ],
        budget_states=[
            {"company_id": "c1", "budget_key": "max_cost_usd", "configured": True, "limit": 1, "used": 2, "available": 0},
        ],
        budget_reservations=[{"state": "COMMITTED", "company_id": "c1", "budget_key": "max_cost_usd", "amount": 2}],
        model_catalogue={"profile_pending_model_count": 3},
    )

    assert report["schema_version"] == EXECUTIVE_RECONCILIATION_SCHEMA
    assert report["coverage"] == {
        "project_count": 2,
        "project_usage_count": 1,
        "worker_run_count": 2,
        "budget_count": 1,
        "budget_reservation_count": 1,
    }
    assert report["projects"]["active_count"] == 1
    assert report["projects"]["usage"]["total_cost_usd"] == 1.25
    assert report["delivery"]["success_rate"] == 1.0
    assert report["budgets"]["overages"][0]["budget_key"] == "max_cost_usd"
    assert [finding["code"] for finding in report["findings"]] == [
        "BUDGET_OVERAGE",
        "MODEL_PROFILE_COVERAGE_PENDING",
        "PROJECT_USAGE_UNAVAILABLE",
    ]
    assert report["views"]["schema_version"] == EXECUTIVE_VIEWS_SCHEMA
    assert report["views"]["cfo"]["status"] == "attention"
    assert report["views"]["cfo"]["overage_count"] == 1
    assert report["views"]["cto"]["failed_worker_runs"] == 0
    assert report["views"]["ceo"]["finding_codes"] == [
        "BUDGET_OVERAGE",
        "MODEL_PROFILE_COVERAGE_PENDING",
        "PROJECT_USAGE_UNAVAILABLE",
    ]


def test_executive_reconciliation_is_stable_for_input_order():
    kwargs = {
        "projects": [{"id": "b", "state": "FAILED"}, {"id": "a", "state": "CREATED"}],
        "project_usage": {"a": {"available": True}, "b": {"available": True}},
        "worker_runs": [{"id": "b", "state": "FAILED"}, {"id": "a", "state": "SUCCEEDED"}],
        "budget_states": [],
    }
    first = build_executive_reconciliation(**kwargs)
    second = build_executive_reconciliation(
        projects=list(reversed(kwargs["projects"])),
        project_usage=kwargs["project_usage"],
        worker_runs=list(reversed(kwargs["worker_runs"])),
        budget_states=[],
    )
    assert first == second


def test_executive_reconciliation_surfaces_reservation_integrity_anomalies():
    report = build_executive_reconciliation(
        projects=[],
        project_usage={},
        worker_runs=[{"id": "run-1", "state": "FAILED"}],
        budget_states=[
            {"company_id": "c1", "budget_key": "max_cost_usd", "configured": True, "limit": 10, "used": 0, "available": 10}
        ],
        budget_reservations=[
            {"id": "r1", "company_id": "c1", "budget_key": "max_cost_usd", "amount": 1, "state": "RESERVED", "run_id": "run-1", "idempotency_key": "same"},
            {"id": "r2", "company_id": "c1", "budget_key": "max_cost_usd", "amount": 1, "state": "BROKEN", "idempotency_key": "same"},
        ],
    )

    codes = [item["code"] for item in report["budgets"]["reservation_reconciliation"]["anomalies"]]
    assert codes == [
        "BUDGET_RESERVATION_SUM_MISMATCH",
        "DUPLICATE_RESERVATION_IDEMPOTENCY_KEY",
        "RESERVED_TERMINAL_RUN",
        "UNKNOWN_RESERVATION_STATE",
    ]
    assert report["status"] == "reconciled_with_findings"

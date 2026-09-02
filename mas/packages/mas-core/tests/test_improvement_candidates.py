"""Tests for bounded self-improvement candidate detection."""

from __future__ import annotations

from decimal import Decimal
from uuid import UUID

import pytest

from mas_core.workflow import (
    ImprovementRisk,
    ImprovementSignal,
    ImprovementSignalSeverity,
    ImprovementSignalSource,
    detect_improvement_candidates,
)

COMPANY_ID = UUID("00000000-0000-4000-a000-000000000901")


def _signal(
    signal_id: str,
    source: ImprovementSignalSource,
    *,
    severity: ImprovementSignalSeverity = ImprovementSignalSeverity.MEDIUM,
    budget_usd: str | None = None,
) -> ImprovementSignal:
    return ImprovementSignal(
        signal_id=signal_id,
        source=source,
        title=f"Investigate {signal_id}",
        description=f"Bounded description for {signal_id}",
        source_ref=f"{source.value}:{signal_id}",
        severity=severity,
        budget_usd=budget_usd,
        company_id=COMPANY_ID,
        licence_metadata={"notice": "metadata-only"},
    )


def test_detector_covers_all_signal_sources_and_is_sorted_by_risk() -> None:
    result = detect_improvement_candidates(
        [
            _signal("operator-1", ImprovementSignalSource.OPERATOR_GOAL, severity=ImprovementSignalSeverity.LOW),
            _signal("defect-1", ImprovementSignalSource.DEFECT, severity=ImprovementSignalSeverity.CRITICAL),
            _signal("metric-1", ImprovementSignalSource.METRIC, severity=ImprovementSignalSeverity.HIGH),
            _signal("upstream-1", ImprovementSignalSource.UPSTREAM_UPDATE),
            _signal("cost-1", ImprovementSignalSource.COST, budget_usd="7.25"),
        ]
    )

    assert len(result.candidates) == 5
    assert [candidate.risk for candidate in result.candidates] == [
        ImprovementRisk.CRITICAL,
        ImprovementRisk.HIGH,
        ImprovementRisk.MEDIUM,
        ImprovementRisk.MEDIUM,
        ImprovementRisk.LOW,
    ]
    assert result.source_counts == {
        "signal:cost": 1,
        "signal:defect": 1,
        "signal:metric": 1,
        "signal:operator_goal": 1,
        "signal:upstream_update": 1,
    }
    assert result.licence_metadata_is_gate is False
    assert all(candidate.licence_metadata == {"notice": "metadata-only"} for candidate in result.candidates)
    assert all(
        item in result.authority_side_effects
        for item in ("no_project_created", "no_budget_reserved", "no_credentials_granted", "no_deployment_changed")
    )


def test_detector_collapses_exact_duplicates_but_rejects_conflicting_reuse() -> None:
    signal = _signal("duplicate-1", ImprovementSignalSource.DEFECT)
    result = detect_improvement_candidates([signal, signal.model_copy(deep=True)])
    assert len(result.candidates) == 1
    assert result.deduplicated_signal_ids == ("duplicate-1",)

    conflicting = signal.model_copy(update={"description": "different evidence"})
    with pytest.raises(ValueError, match="reused with different evidence"):
        detect_improvement_candidates([signal, conflicting])


def test_detector_preserves_explicit_budget_and_only_previews_projects() -> None:
    result = detect_improvement_candidates(
        [_signal("budget-1", ImprovementSignalSource.COST, budget_usd="12.50")]
    )
    assert result.candidates[0].budget_usd == Decimal("12.50")
    preview = result.canonical_project_requests()
    assert len(preview) == 1
    assert preview[0]["project_id"]
    assert preview[0]["config"]["self_improvement"]["schema_version"] == "aiat.self-improvement.v1"


def test_signal_budget_and_metadata_are_bounded() -> None:
    with pytest.raises(ValueError, match="between 0 and 100000"):
        _signal("too-large", ImprovementSignalSource.COST, budget_usd="100001")
    with pytest.raises(ValueError, match="20 entries"):
        ImprovementSignal(
            signal_id="metadata-too-large",
            source=ImprovementSignalSource.METRIC,
            title="bounded",
            description="bounded",
            source_ref="metric:bounded",
            metadata={str(index): "value" for index in range(21)},
        )

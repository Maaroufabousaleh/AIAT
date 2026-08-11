"""Unit tests for the persisted dashboard section policy."""

from __future__ import annotations

import pytest

from mas_core.policy.dashboard_access import (
    DASHBOARD_SECTIONS,
    DEFAULT_DASHBOARD_SECTION_ACL,
    normalize_dashboard_acl,
    principal_can_access_section,
    serialize_dashboard_acl,
)


def test_operator_is_always_the_repair_principal() -> None:
    acl = normalize_dashboard_acl({"operator": [], "ceo": ["projects"]})

    assert set(acl["operator"]) == set(DASHBOARD_SECTIONS)
    assert principal_can_access_section("ceo", "projects", acl)
    assert not principal_can_access_section("ceo", "credentials", acl)


def test_persisted_json_is_deterministic_and_complete() -> None:
    serialized = serialize_dashboard_acl({"worker": ["workers", "projects"]})
    normalized = normalize_dashboard_acl(serialized)

    assert normalized["worker"] == frozenset({"workers", "projects"})
    assert set(normalized) == set(DEFAULT_DASHBOARD_SECTION_ACL)
    assert serialized == serialize_dashboard_acl(normalized)


@pytest.mark.parametrize(
    "value",
    [
        {"unknown": ["projects"]},
        {"worker": ["not-a-dashboard-section"]},
        {"worker": "projects"},
        "not-json",
    ],
)
def test_malformed_acl_is_rejected(value: object) -> None:
    with pytest.raises(ValueError):
        normalize_dashboard_acl(value)  # type: ignore[arg-type]

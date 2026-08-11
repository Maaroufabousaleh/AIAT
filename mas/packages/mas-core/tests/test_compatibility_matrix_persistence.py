"""Compatibility-matrix persistence boundary coverage."""

from __future__ import annotations

from mas_core.worker_registry.steward import CompatibilityMatrix


def test_persisted_profile_and_capability_shapes_are_normalized() -> None:
    matrix = CompatibilityMatrix(
        runtime_version="1.0.0",
        adapter_version="1.0.0",
        contract_version="aiat.adapter.v1",
        model_profiles={"worker": "profile-v1", "fallback": ["profile-v2", "profile-v3"]},
        capabilities={"required_model_capabilities": ["tool_calling"], "streaming": True},
    )

    assert matrix.model_profiles == {
        "worker": ("profile-v1",),
        "fallback": ("profile-v2", "profile-v3"),
    }
    assert matrix.capabilities == {
        "required_model_capabilities": ["tool_calling"],
        "streaming": True,
    }

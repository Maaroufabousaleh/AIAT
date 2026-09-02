from __future__ import annotations

import types

from mas_core.worker_registry.maf_compatibility import (
    MAF_COMPATIBILITY_SCHEMA,
    evaluate_microsoft_agent_framework_compatibility,
)


def test_maf_compatibility_preflight_accepts_locked_versions_and_symbols() -> None:
    report = evaluate_microsoft_agent_framework_compatibility(
        module=types.SimpleNamespace(Agent=object),
        package_version="1.13.0",
        mcp_version="1.27.0",
    )

    assert report.ready is True
    assert report.as_dict()["schema_version"] == MAF_COMPATIBILITY_SCHEMA
    assert report.as_dict()["licence_metadata_is_gate"] is False


def test_maf_compatibility_preflight_blocks_workspace_mcp_mismatch() -> None:
    report = evaluate_microsoft_agent_framework_compatibility(
        module=types.SimpleNamespace(Agent=object),
        package_version="1.13.0",
        mcp_version="1.23.3",
    )

    assert report.ready is False
    assert any("mcp" in blocker for blocker in report.blockers)


def test_maf_compatibility_preflight_blocks_missing_runtime_package_and_symbol() -> None:
    report = evaluate_microsoft_agent_framework_compatibility(
        module=types.SimpleNamespace(),
        package_version=None,
        mcp_version=None,
    )

    assert report.ready is False
    assert any("agent-framework" in blocker for blocker in report.blockers)
    assert any("Agent/ChatAgent" in blocker for blocker in report.blockers)

"""Security-contract tests for the OpenHands-only signed bridge grant."""

from __future__ import annotations

from uuid import uuid4

import pytest

from mas_core.worker_contract.openhands_bridge import (
    OpenHandsToolGrantError,
    issue_openhands_tool_grant,
    verify_openhands_tool_grant,
)


def test_grant_binds_aiat_identity_and_tool_allowlist() -> None:
    run_id = uuid4()
    token = issue_openhands_tool_grant(
        "bridge-secret",
        worker_id="worker-openhands",
        run_id=run_id,
        project_id=None,
        tool_names=["repository.read", "tests.execute", "repository.read"],
        ttl_seconds=60,
        now=100,
    )
    grant = verify_openhands_tool_grant(token, "bridge-secret", now=110)
    assert grant.worker_id == "worker-openhands"
    assert grant.run_id == run_id
    assert grant.tool_names == frozenset({"repository.read", "tests.execute"})


def test_grant_rejects_tampering_and_expiry() -> None:
    token = issue_openhands_tool_grant(
        "bridge-secret",
        worker_id="worker-openhands",
        run_id=uuid4(),
        project_id=None,
        tool_names=[],
        ttl_seconds=30,
        now=100,
    )
    with pytest.raises(OpenHandsToolGrantError):
        verify_openhands_tool_grant(token + "x", "bridge-secret", now=110)
    with pytest.raises(OpenHandsToolGrantError, match="expired"):
        verify_openhands_tool_grant(token, "bridge-secret", now=130)


def test_grant_rejects_wrong_bridge_secret() -> None:
    token = issue_openhands_tool_grant(
        "bridge-secret",
        worker_id="worker-openhands",
        run_id=uuid4(),
        project_id=None,
        tool_names=["repository.read"],
        now=100,
    )
    with pytest.raises(OpenHandsToolGrantError):
        verify_openhands_tool_grant(token, "another-secret", now=110)

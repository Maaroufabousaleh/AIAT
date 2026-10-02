from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from check_worker_host_recovery_postgres import (
    HOST_ID,
    _recover_fixture_host,
    build_report,
)

MAS_ROOT = Path(__file__).resolve().parents[1]


def test_worker_host_recovery_checker_fails_closed_without_database_configuration() -> None:
    report = build_report(dsn="")

    assert report["status"] == "blocked"
    assert report["reason"] == "worker_host_recovery_evidence_database_not_configured"
    assert report["mutation_performed"] is False
    assert report["external_provider_mutation_performed"] is False
    assert report["worker_dispatch_performed"] is False
    assert report["licence_metadata_is_gate"] is False


@pytest.mark.asyncio
async def test_worker_host_recovery_checker_scopes_reconciliation_to_its_fixture() -> None:
    recovery = SimpleNamespace(
        reconcile_expired_hosts=AsyncMock(return_value={"status": "RECOVERED"})
    )

    report = await _recover_fixture_host(recovery)

    assert report["status"] == "RECOVERED"
    recovery.reconcile_expired_hosts.assert_awaited_once_with(host_ids=[HOST_ID])

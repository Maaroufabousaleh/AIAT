from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from orchestrator_api.main import _classify_pm_dead_letters


def test_unresolved_dead_letters_block():
    rows = [{"id": "active"}, {"id": "historical"}]
    dispositions = [{"outbox_id": "historical", "disposition": "SUPERSEDED"}]

    assert _classify_pm_dead_letters(rows, dispositions) == [{"id": "active"}]


def test_error_text_alone_does_not_resolve_dead_letter():
    rows = [{"id": "still-active", "last_error": "SUPERSEDED by a replacement"}]

    assert _classify_pm_dead_letters(rows, []) == rows


@pytest.mark.anyio
async def test_lifecycle_gate_uses_exhaustive_dead_letter_count(monkeypatch):
    from orchestrator_api import main

    storage = MagicMock()
    storage.list_pm_reconciliation_runs = AsyncMock(
        return_value=[
            {
                "id": uuid4(),
                "status": "COMPLETED",
                "counts": {
                    "mapped": 1001,
                    "seen": 1001,
                    "drift": 0,
                    "conflicts": 0,
                    "scope_conflicts": 0,
                    "version_mismatches": 0,
                    "hash_mismatches": 0,
                },
            }
        ]
    )
    storage.list_pm_conflicts = AsyncMock(return_value=[])
    storage.list_pm_outbox = AsyncMock(return_value=[])
    storage.get_pm_outbox_dead_letter_counts = AsyncMock(
        return_value={"active": 1, "historical": 1000, "total": 1001}
    )
    tls_context = MagicMock(verify_mode=main.ssl.CERT_REQUIRED, check_hostname=True)
    monkeypatch.setattr(main, "provider_ssl_context", lambda: tls_context)

    snapshot, blockers = await main._lifecycle_gate_snapshot(
        storage,
        connection_id=uuid4(),
        binding=None,
        doctor={"ready": True, "blockers": []},
    )

    assert "active PM dead letters exist" in blockers
    assert snapshot["active_dead_letters"] == 1
    assert snapshot["historical_dead_letters"] == 1000
    storage.get_pm_outbox_dead_letter_counts.assert_awaited_once()


@pytest.mark.anyio
async def test_canary_audit_omits_expiry_for_non_expired_plan(client):
    from orchestrator_api.main import app

    plan_id = uuid4()
    digest = "a" * 64
    storage = MagicMock()
    storage.get_pm_inbound_canary_plan = AsyncMock(
        return_value={
            "id": plan_id,
            "digest": digest,
            "status": "ARMED",
            "connection_id": uuid4(),
            "binding_id": uuid4(),
            "project_id": uuid4(),
            "created_by": "creator",
            "updated_at": datetime.now(tz=UTC),
        }
    )
    storage.record_integration_evidence = AsyncMock()
    app.state.storage = storage

    response = await client.post(
        f"/integrations/inbound-canaries/{plan_id}/audit-evidence",
        json={"digest": digest},
        headers={"X-API-Key": "test-operator-key"},
    )

    assert response.status_code == 200
    assert response.json()["evidence"] == {}
    storage.record_integration_evidence.assert_not_awaited()


@pytest.mark.anyio
async def test_canary_audit_uses_persisted_expiry_attribution(client):
    from orchestrator_api.main import app

    plan_id = uuid4()
    digest = "b" * 64
    expired_at = datetime.now(tz=UTC)
    storage = MagicMock()
    storage.get_pm_inbound_canary_plan = AsyncMock(
        return_value={
            "id": plan_id,
            "digest": digest,
            "status": "EXPIRED",
            "connection_id": uuid4(),
            "binding_id": uuid4(),
            "project_id": uuid4(),
            "created_by": "creator",
            "updated_at": expired_at,
            "expired_by": "operator",
            "expired_at": expired_at,
        }
    )
    evidence_id = uuid4()
    storage.record_integration_evidence = AsyncMock(return_value={"id": evidence_id})
    app.state.storage = storage

    response = await client.post(
        f"/integrations/inbound-canaries/{plan_id}/audit-evidence",
        json={"digest": digest},
        headers={"X-API-Key": "test-operator-key"},
    )

    assert response.status_code == 200
    assert response.json()["evidence"] == {"expiry": str(evidence_id)}
    payload = storage.record_integration_evidence.await_args.kwargs["payload"]
    assert payload["actor"] == "operator"
    assert payload["occurred_at"] == expired_at.isoformat()

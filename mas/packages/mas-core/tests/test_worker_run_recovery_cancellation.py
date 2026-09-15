"""Regression coverage for durable cancellation during worker-run recovery."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from mas_core.memory.storage import AgentStorage


class _Result:
    def __init__(self, rows: list[dict[str, object]]) -> None:
        self._rows = rows

    def mappings(self) -> _Result:
        return self

    def all(self) -> list[dict[str, object]]:
        return self._rows

    def first(self) -> dict[str, object] | None:
        return self._rows[0] if self._rows else None


@pytest.mark.asyncio
async def test_recovery_settles_durable_cancellation_instead_of_requeueing() -> None:
    run_id = uuid4()
    expired_at = datetime.now(tz=UTC) - timedelta(seconds=1)
    row = {
        "id": run_id,
        "state": "RUNNING",
        "attempt_count": 2,
        "cancel_requested_at": expired_at,
        "lease_expires_at": expired_at,
    }

    connection = MagicMock()
    connection.execute = AsyncMock(
        side_effect=[
            _Result([row]),
            MagicMock(rowcount=1),
            MagicMock(rowcount=1),
        ]
    )
    transaction = MagicMock()
    transaction.__aenter__ = AsyncMock(return_value=connection)
    transaction.__aexit__ = AsyncMock(return_value=False)
    engine = MagicMock()
    engine.begin.return_value = transaction

    storage = AgentStorage.__new__(AgentStorage)
    storage._engine = engine

    recovered = await storage.recover_expired_worker_runs()

    assert recovered[0]["id"] == run_id
    assert recovered[0]["state"] == "CANCELLED"
    assert recovered[0]["claim_owner"] is None
    assert recovered[0]["lease_expires_at"] is None
    assert "not requeued" in str(recovered[0]["recovery_reason"])

    cancellation_update = connection.execute.await_args_list[1].args[0]
    assert cancellation_update.compile().params["state"] == "CANCELLED"
    assert cancellation_update.compile().params["next_attempt_at"] is None

    transition_insert = connection.execute.await_args_list[2].args[0]
    assert transition_insert.compile().params["from_state"] == "RUNNING"
    assert transition_insert.compile().params["to_state"] == "CANCELLED"
    assert connection.execute.await_count == 3


@pytest.mark.asyncio
async def test_claim_excludes_queued_runs_with_durable_cancellation_marker() -> None:
    connection = MagicMock()
    connection.execute = AsyncMock(return_value=_Result([]))
    transaction = MagicMock()
    transaction.__aenter__ = AsyncMock(return_value=connection)
    transaction.__aexit__ = AsyncMock(return_value=False)
    engine = MagicMock()
    engine.begin.return_value = transaction

    storage = AgentStorage.__new__(AgentStorage)
    storage._engine = engine

    assert await storage.claim_worker_run(owner="host-executor") is None

    query = connection.execute.await_args.args[0]
    assert "cancel_requested_at IS NULL" in str(query)


@pytest.mark.asyncio
async def test_recovery_settles_queued_run_with_durable_cancellation_marker() -> None:
    run_id = uuid4()
    row = {
        "id": run_id,
        "state": "QUEUED",
        "attempt_count": 0,
        "cancel_requested_at": datetime.now(tz=UTC),
        "lease_expires_at": None,
    }

    connection = MagicMock()
    connection.execute = AsyncMock(
        side_effect=[
            _Result([row]),
            MagicMock(rowcount=1),
            MagicMock(rowcount=1),
        ]
    )
    transaction = MagicMock()
    transaction.__aenter__ = AsyncMock(return_value=connection)
    transaction.__aexit__ = AsyncMock(return_value=False)
    engine = MagicMock()
    engine.begin.return_value = transaction

    storage = AgentStorage.__new__(AgentStorage)
    storage._engine = engine

    recovered = await storage.recover_expired_worker_runs()

    assert recovered[0]["state"] == "CANCELLED"
    cancellation_update = connection.execute.await_args_list[1].args[0]
    transition_insert = connection.execute.await_args_list[2].args[0]
    assert cancellation_update.compile().params["state"] == "CANCELLED"
    assert transition_insert.compile().params["from_state"] == "QUEUED"
    assert transition_insert.compile().params["to_state"] == "CANCELLED"

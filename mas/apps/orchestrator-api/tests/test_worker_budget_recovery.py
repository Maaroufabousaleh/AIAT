from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

import pytest


class _BudgetStorage:
    def __init__(self) -> None:
        self.created_at = datetime(2026, 10, 12, tzinfo=UTC)
        self.run_id = uuid4()
        self.reservation_id = uuid4()
        self.active_reservation_id = uuid4()
        self.actual_cost_usd: str | None = "0.75"
        self.reservations = {
            self.reservation_id: {
                "state": "RESERVED",
                "budget_key": "max_cost_usd",
                "amount": Decimal("2.00"),
                "committed_count": 0,
            },
            self.active_reservation_id: {
                "state": "RESERVED",
                "budget_key": "max_concurrent_runs",
                "amount": Decimal("1"),
                "committed_count": 0,
            },
        }
        self.fail_next_settlement = True
        self.settlement_calls: list[tuple[UUID, str, Decimal | None]] = []
        self.query_calls: list[dict[str, object]] = []

    async def list_terminal_worker_budget_reservations(self, **query):
        self.query_calls.append(query)
        # Deliberately include an active row to verify the API reconciler's
        # independent terminal-state guard as well as storage-side filtering.
        rows = [
            {
                "reservation_id": self.reservation_id,
                "run_id": self.run_id,
                "budget_key": "max_cost_usd",
                "reservation_created_at": self.created_at,
                "run_state": "SUCCEEDED",
                "actual_cost_usd": self.actual_cost_usd,
            },
            {
                "reservation_id": self.active_reservation_id,
                "run_id": uuid4(),
                "budget_key": "max_concurrent_runs",
                "reservation_created_at": self.created_at + timedelta(seconds=1),
                "run_state": "RUNNING",
                "actual_cost_usd": None,
            },
        ]
        return [
            row
            for row in rows
            if self.reservations[row["reservation_id"]]["state"] == "RESERVED"
        ][: int(query["limit"])]

    async def settle_budget_reservation(
        self, reservation_id, *, state, amount=None, metadata=None
    ):
        self.settlement_calls.append((reservation_id, state, amount))
        if self.fail_next_settlement:
            self.fail_next_settlement = False
            raise RuntimeError("temporary settlement failure")
        reservation = self.reservations[reservation_id]
        if reservation["state"] != "RESERVED":
            return reservation
        reservation["state"] = state
        if state == "COMMITTED":
            reservation["amount"] = amount
            reservation["committed_count"] += 1
        return reservation


@pytest.mark.anyio
async def test_terminal_budget_reconciler_retries_idempotently_and_skips_active_runs() -> None:
    import orchestrator_api.main as main

    storage = _BudgetStorage()

    processed, cursor = await main.reconcile_terminal_worker_budget_reservations(
        storage,
        limit=10,
    )
    assert processed == 1
    assert cursor is None
    assert storage.reservations[storage.reservation_id]["state"] == "RESERVED"
    assert storage.reservations[storage.active_reservation_id]["state"] == "RESERVED"
    assert [call[0] for call in storage.settlement_calls] == [storage.reservation_id]
    assert storage.settlement_calls[0][2] == Decimal("0.75")

    # The next bounded pass retries the unsettled row. Settlement is the same
    # canonical operation used in the live terminal callback.
    processed, cursor = await main.reconcile_terminal_worker_budget_reservations(
        storage,
        limit=10,
    )
    assert processed == 1
    assert cursor is None
    assert storage.reservations[storage.reservation_id]["state"] == "COMMITTED"
    assert storage.reservations[storage.reservation_id]["amount"] == Decimal("0.75")
    assert storage.reservations[storage.reservation_id]["committed_count"] == 1
    assert storage.reservations[storage.active_reservation_id]["state"] == "RESERVED"
    assert all(call[0] == storage.reservation_id for call in storage.settlement_calls)

    # Settled rows disappear from the durable query, so replay does not commit
    # the amount again.
    processed, cursor = await main.reconcile_terminal_worker_budget_reservations(
        storage,
        limit=10,
    )
    assert processed == 0
    assert cursor is None
    assert storage.reservations[storage.reservation_id]["committed_count"] == 1


@pytest.mark.anyio
async def test_terminal_budget_reconciler_does_not_infer_cost_when_usage_is_missing() -> None:
    import orchestrator_api.main as main

    storage = _BudgetStorage()
    storage.actual_cost_usd = None
    storage.fail_next_settlement = False

    processed, cursor = await main.reconcile_terminal_worker_budget_reservations(
        storage,
        limit=10,
    )

    assert processed == 1
    assert cursor is None
    assert storage.settlement_calls == [(storage.reservation_id, "RELEASED", None)]
    assert storage.reservations[storage.reservation_id]["state"] == "RELEASED"
    assert storage.reservations[storage.reservation_id]["committed_count"] == 0


@pytest.mark.anyio
async def test_terminal_budget_reconciler_advances_full_pages_with_keyset_cursor() -> None:
    import orchestrator_api.main as main

    storage = _BudgetStorage()
    # Two rows force the cursor path; the second is active and must not settle.
    processed, cursor = await main.reconcile_terminal_worker_budget_reservations(
        storage,
        limit=2,
    )

    assert processed == 1
    assert cursor == (
        storage.created_at + timedelta(seconds=1),
        storage.active_reservation_id,
    )
    assert storage.query_calls == [{"limit": 2}]
    assert all(call[0] == storage.reservation_id for call in storage.settlement_calls)

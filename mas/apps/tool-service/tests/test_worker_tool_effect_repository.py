"""SQL-boundary checks for the tool service's worker-effect repository."""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import pytest
from tool_service.usage import WorkerToolEffectRepository


class _Connection:
    def __init__(
        self,
        row: dict[str, Any] | None = None,
        worker_run: dict[str, Any] | None = None,
    ) -> None:
        self.row = row
        self.rows = [row] if row is not None else []
        self.worker_run = worker_run
        self.calls: list[tuple[str, tuple[Any, ...]]] = []

    @asynccontextmanager
    async def transaction(self):
        yield

    async def execute(self, query: str, *args: Any) -> str:
        self.calls.append((query, args))
        if query.lstrip().startswith("INSERT INTO worker_tool_effects"):
            existing = next(
                (
                    row
                    for row in self.rows
                    if row["run_id"] == args[1]
                    and row["idempotency_key"] == args[3]
                ),
                None,
            )
            if existing is None:
                self.row = {
                    "id": args[0],
                    "run_id": args[1],
                    "request_id": args[2],
                    "idempotency_key": args[3],
                    "tool_name": args[4],
                    "request_sha256": args[5],
                    "state": "IN_FLIGHT",
                    "response_json": None,
                }
                self.rows.append(self.row)
        return "INSERT 0 1"

    async def fetchrow(self, query: str, *args: Any) -> dict[str, Any] | None:
        self.calls.append((query, args))
        if query.lstrip().startswith("SELECT id, state, claim_owner"):
            if self.worker_run is None:
                return {
                    "id": args[0],
                    "state": "RUNNING",
                    "claim_owner": None,
                    "attempt_count": 0,
                    "lease_expires_at": None,
                }
            if self.worker_run is not None and self.worker_run["id"] == args[0]:
                return dict(self.worker_run)
            return None
        if "state IN ('IN_FLIGHT', 'AMBIGUOUS')" in query:
            row = next(
                (
                    row
                    for row in self.rows
                    if row["run_id"] == args[0]
                    and row["state"] in {"IN_FLIGHT", "AMBIGUOUS"}
                ),
                None,
            )
            return dict(row) if row is not None else None
        if query.lstrip().startswith("SELECT id, run_id, request_id"):
            row = next(
                (
                    row
                    for row in self.rows
                    if row["run_id"] == args[0]
                    and row["idempotency_key"] == args[1]
                ),
                None,
            )
            return dict(row) if row is not None else None
        if query.lstrip().startswith("UPDATE worker_tool_effects"):
            row = next(
                (
                    row
                    for row in self.rows
                    if row["id"] == args[0]
                    and row["run_id"] == args[1]
                    and row["state"] == "IN_FLIGHT"
                ),
                None,
            )
            if row is None:
                return None
            row.update(
                {
                    "state": args[2],
                    "response_json": json.loads(args[3]),
                    "updated_at": args[4],
                    "completed_at": args[4] if args[2] == "COMPLETED" else None,
                }
            )
            self.row = row
            return dict(row)
        raise AssertionError(f"unexpected SQL: {query}")


class _Acquire:
    def __init__(self, connection: _Connection) -> None:
        self.connection = connection

    async def __aenter__(self) -> _Connection:
        return self.connection

    async def __aexit__(self, *_: object) -> None:
        return None


class _Pool:
    def __init__(self, connection: _Connection) -> None:
        self.connection = connection

    def acquire(self) -> _Acquire:
        return _Acquire(self.connection)


def _inputs() -> dict[str, Any]:
    return {
        "run_id": uuid4(),
        "request_id": uuid4(),
        "idempotency_key": str(uuid4()),
        "tool_name": "repository.read",
        "request_sha256": "a" * 64,
        "claim_owner": None,
        "attempt_count": 0,
    }


@pytest.mark.asyncio
async def test_begin_inserts_before_select_and_classifies_new_row() -> None:
    inputs = _inputs()
    connection = _Connection(
        worker_run={
            "id": inputs["run_id"],
            "state": "RUNNING",
            "claim_owner": None,
            "attempt_count": 0,
            "lease_expires_at": None,
        }
    )
    repository = WorkerToolEffectRepository(_Pool(connection))  # type: ignore[arg-type]

    effect = await repository.begin_worker_tool_effect(**inputs)

    assert effect["disposition"] == "NEW"
    assert effect["state"] == "IN_FLIGHT"
    assert effect["run_id"] == inputs["run_id"]
    assert effect["request_id"] == inputs["request_id"]
    assert effect["idempotency_key"] == inputs["idempotency_key"]
    assert [query.lstrip().split(None, 2)[0:2] for query, _ in connection.calls] == [
        ["SELECT", "id,"],
        ["SELECT", "idempotency_key,"],
        ["INSERT", "INTO"],
        ["SELECT", "id,"],
    ]
    assert "FROM worker_runs" in connection.calls[0][0]
    assert "FOR UPDATE" in connection.calls[0][0]
    assert "state IN ('IN_FLIGHT', 'AMBIGUOUS')" in connection.calls[1][0]
    assert "response_json" not in connection.calls[1][0]
    assert "FOR UPDATE" in connection.calls[1][0]
    assert "ON CONFLICT (run_id, idempotency_key) DO NOTHING" in connection.calls[2][0]
    assert "FOR UPDATE" in connection.calls[3][0]


@pytest.mark.asyncio
async def test_new_key_is_blocked_while_run_has_unresolved_effect() -> None:
    inputs = _inputs()
    inputs["idempotency_key"] = "new-session-retry"
    pending = {
        "id": uuid4(),
        "run_id": inputs["run_id"],
        "request_id": uuid4(),
        "idempotency_key": "previous-session-request",
        "tool_name": "repository.write",
        "request_sha256": "b" * 64,
        "state": "IN_FLIGHT",
        "response_json": None,
    }
    connection = _Connection(
        row=pending,
        worker_run={
            "id": inputs["run_id"],
            "state": "RUNNING",
            "claim_owner": None,
            "attempt_count": 0,
            "lease_expires_at": None,
        },
    )
    repository = WorkerToolEffectRepository(_Pool(connection))  # type: ignore[arg-type]

    effect = await repository.begin_worker_tool_effect(**inputs)

    assert effect == {"disposition": "AMBIGUOUS"}
    assert len(connection.calls) == 2
    assert all("INSERT INTO worker_tool_effects" not in query for query, _ in connection.calls)


@pytest.mark.asyncio
async def test_new_key_is_allowed_after_prior_effect_is_completed() -> None:
    inputs = _inputs()
    completed = {
        "id": uuid4(),
        "run_id": inputs["run_id"],
        "request_id": uuid4(),
        "idempotency_key": "prior-completed-request",
        "tool_name": "repository.write",
        "request_sha256": "b" * 64,
        "state": "COMPLETED",
        "response_json": {"success": True},
    }
    connection = _Connection(
        row=completed,
        worker_run={
            "id": inputs["run_id"],
            "state": "RUNNING",
            "claim_owner": None,
            "attempt_count": 0,
            "lease_expires_at": None,
        },
    )
    repository = WorkerToolEffectRepository(_Pool(connection))  # type: ignore[arg-type]

    effect = await repository.begin_worker_tool_effect(**inputs)

    assert effect["disposition"] == "NEW"
    assert effect["idempotency_key"] == inputs["idempotency_key"]
    assert len(connection.rows) == 2
    assert connection.rows[0]["state"] == "COMPLETED"
    assert connection.rows[1]["state"] == "IN_FLIGHT"


@pytest.mark.asyncio
async def test_begin_replays_a_known_completed_row() -> None:
    inputs = _inputs()
    row = {
        "id": uuid4(),
        **inputs,
        "state": "COMPLETED",
        "response_json": {"success": True},
    }
    repository = WorkerToolEffectRepository(_Pool(_Connection(row)))  # type: ignore[arg-type]

    effect = await repository.begin_worker_tool_effect(**inputs)

    assert effect["disposition"] == "REPLAY"
    assert effect["response_json"] == {"success": True}


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["IN_FLIGHT", "AMBIGUOUS"])
async def test_begin_marks_existing_unfinished_rows_ambiguous(state: str) -> None:
    inputs = _inputs()
    row = {"id": uuid4(), **inputs, "state": state, "response_json": None}
    repository = WorkerToolEffectRepository(_Pool(_Connection(row)))  # type: ignore[arg-type]

    effect = await repository.begin_worker_tool_effect(**inputs)

    assert effect["disposition"] == "AMBIGUOUS"


@pytest.mark.asyncio
async def test_begin_classifies_changed_payload_as_conflict() -> None:
    inputs = _inputs()
    row = {
        "id": uuid4(),
        **{**inputs, "request_sha256": "b" * 64},
        "state": "COMPLETED",
        "response_json": {"success": True},
    }
    repository = WorkerToolEffectRepository(_Pool(_Connection(row)))  # type: ignore[arg-type]

    effect = await repository.begin_worker_tool_effect(**inputs)

    assert effect["disposition"] == "CONFLICT"


@pytest.mark.asyncio
async def test_begin_rejects_reclaimed_attempt_before_effect_insert() -> None:
    inputs = _inputs()
    inputs.update({"claim_owner": "executor-old", "attempt_count": 1})
    connection = _Connection(
        worker_run={
            "id": inputs["run_id"],
            "state": "RUNNING",
            "claim_owner": "executor-new",
            "attempt_count": 2,
            "lease_expires_at": datetime.now(tz=UTC) + timedelta(minutes=2),
        }
    )
    repository = WorkerToolEffectRepository(_Pool(connection))  # type: ignore[arg-type]

    effect = await repository.begin_worker_tool_effect(**inputs)

    assert effect["disposition"] == "CLAIM_LOST"
    assert len(connection.calls) == 1
    assert "FOR UPDATE" in connection.calls[0][0]


@pytest.mark.asyncio
async def test_dispatch_claim_check_rejects_expired_or_reassigned_attempt() -> None:
    run_id = uuid4()
    connection = _Connection(
        worker_run={
            "id": run_id,
            "state": "RUNNING",
            "claim_owner": "executor-new",
            "attempt_count": 2,
            "lease_expires_at": datetime.now(tz=UTC) + timedelta(minutes=2),
        }
    )
    repository = WorkerToolEffectRepository(_Pool(connection))  # type: ignore[arg-type]

    current = await repository.validate_worker_run_claim(
        run_id=run_id,
        claim_owner="executor-old",
        attempt_count=1,
    )

    assert current is False
    assert len(connection.calls) == 1
    assert "FOR UPDATE" in connection.calls[0][0]


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["validate", "begin"])
async def test_cancel_requested_claim_cannot_validate_or_reserve_tool_effect(
    operation: str,
) -> None:
    inputs = _inputs()
    inputs.update({"claim_owner": "executor-a", "attempt_count": 3})
    connection = _Connection(
        worker_run={
            "id": inputs["run_id"],
            "state": "RUNNING",
            "claim_owner": "executor-a",
            "attempt_count": 3,
            "lease_expires_at": datetime.now(tz=UTC) + timedelta(minutes=1),
            "cancel_requested_at": datetime.now(tz=UTC),
        }
    )
    repository = WorkerToolEffectRepository(_Pool(connection))  # type: ignore[arg-type]

    if operation == "validate":
        current = await repository.validate_worker_run_claim(
            run_id=inputs["run_id"],
            claim_owner="executor-a",
            attempt_count=3,
        )
        assert current is False
    else:
        effect = await repository.begin_worker_tool_effect(**inputs)
        assert effect["disposition"] == "CLAIM_LOST"

    assert len(connection.calls) == 1
    assert "cancel_requested_at" in connection.calls[0][0]
    assert "FOR UPDATE" in connection.calls[0][0]


@pytest.mark.asyncio
async def test_completion_updates_only_an_in_flight_row() -> None:
    inputs = _inputs()
    inputs.update({"claim_owner": "executor-a", "attempt_count": 3})
    connection = _Connection(
        worker_run={
            "id": inputs["run_id"],
            "state": "RUNNING",
            "claim_owner": "executor-a",
            "attempt_count": 3,
            "lease_expires_at": datetime.now(tz=UTC) + timedelta(minutes=2),
            "cancel_requested_at": None,
        }
    )
    repository = WorkerToolEffectRepository(_Pool(connection))  # type: ignore[arg-type]
    effect = await repository.begin_worker_tool_effect(**inputs)
    response = {"tool_name": "repository.read", "success": True}

    settled = await repository.complete_worker_tool_effect(
        effect["id"],
        run_id=inputs["run_id"],
        claim_owner=inputs["claim_owner"],
        attempt_count=inputs["attempt_count"],
        state="COMPLETED",
        response_json=response,
    )
    second_settlement = await repository.complete_worker_tool_effect(
        effect["id"],
        run_id=inputs["run_id"],
        claim_owner=inputs["claim_owner"],
        attempt_count=inputs["attempt_count"],
        state="AMBIGUOUS",
        response_json={"success": False},
    )

    assert settled is not None
    assert settled["state"] == "COMPLETED"
    assert settled["response_json"] == response
    assert second_settlement is None
    completion_calls = connection.calls[4:]
    assert "FROM worker_runs" in completion_calls[0][0]
    assert "FOR UPDATE" in completion_calls[0][0]
    assert "cancel_requested_at" in completion_calls[0][0]
    update_query = completion_calls[1][0]
    assert "WHERE id = $1 AND run_id = $2 AND state = 'IN_FLIGHT'" in update_query
    assert completion_calls[0][1] == (inputs["run_id"],)
    assert completion_calls[1][1][:3] == (
        effect["id"],
        inputs["run_id"],
        "COMPLETED",
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    (
        "worker_owner",
        "worker_attempt",
        "lease_expires_at",
        "cancel_requested_at",
        "completion_owner",
        "completion_attempt",
    ),
    [
        (
            "executor-new",
            2,
            datetime.now(tz=UTC) + timedelta(minutes=2),
            None,
            "executor-old",
            1,
        ),
        (
            "executor-a",
            3,
            datetime.now(tz=UTC) - timedelta(seconds=1),
            None,
            "executor-a",
            3,
        ),
        (
            "executor-a",
            3,
            datetime.now(tz=UTC) + timedelta(minutes=2),
            datetime.now(tz=UTC),
            "executor-a",
            3,
        ),
    ],
    ids=["reassigned-claim", "expired-lease", "cancellation-requested"],
)
async def test_completion_rejects_stale_claim_and_leaves_effect_in_flight(
    worker_owner: str,
    worker_attempt: int,
    lease_expires_at: datetime,
    cancel_requested_at: datetime | None,
    completion_owner: str,
    completion_attempt: int,
) -> None:
    run_id = uuid4()
    effect_id = uuid4()
    connection = _Connection(
        row={
            "id": effect_id,
            "run_id": run_id,
            "request_id": uuid4(),
            "idempotency_key": str(uuid4()),
            "tool_name": "repository.read",
            "request_sha256": "a" * 64,
            "state": "IN_FLIGHT",
            "response_json": None,
        },
        worker_run={
            "id": run_id,
            "state": "RUNNING",
            "claim_owner": worker_owner,
            "attempt_count": worker_attempt,
            "lease_expires_at": lease_expires_at,
            "cancel_requested_at": cancel_requested_at,
        },
    )
    repository = WorkerToolEffectRepository(_Pool(connection))  # type: ignore[arg-type]

    settled = await repository.complete_worker_tool_effect(
        effect_id,
        run_id=run_id,
        claim_owner=completion_owner,
        attempt_count=completion_attempt,
        state="COMPLETED",
        response_json={"success": True},
    )

    assert settled is None
    assert connection.row is not None
    assert connection.row["state"] == "IN_FLIGHT"
    assert len(connection.calls) == 1
    assert "FROM worker_runs" in connection.calls[0][0]
    assert "FOR UPDATE" in connection.calls[0][0]


@pytest.mark.asyncio
async def test_completion_rejects_missing_claim_fence() -> None:
    run_id = uuid4()
    effect_id = uuid4()
    connection = _Connection(
        row={
            "id": effect_id,
            "run_id": run_id,
            "request_id": uuid4(),
            "idempotency_key": str(uuid4()),
            "tool_name": "repository.read",
            "request_sha256": "a" * 64,
            "state": "IN_FLIGHT",
            "response_json": None,
        }
    )
    repository = WorkerToolEffectRepository(_Pool(connection))  # type: ignore[arg-type]

    settled = await repository.complete_worker_tool_effect(
        effect_id,
        run_id=run_id,
        claim_owner=None,
        attempt_count=0,
        state="COMPLETED",
        response_json={"success": True},
    )

    assert settled is None
    assert connection.row is not None
    assert connection.row["state"] == "IN_FLIGHT"
    assert connection.calls == []


@pytest.mark.asyncio
async def test_ambiguous_completion_cannot_persist_a_success_response() -> None:
    run_id = uuid4()
    effect_id = uuid4()
    connection = _Connection(
        row={
            "id": effect_id,
            "run_id": run_id,
            "request_id": uuid4(),
            "idempotency_key": str(uuid4()),
            "tool_name": "repository.read",
            "request_sha256": "a" * 64,
            "state": "IN_FLIGHT",
            "response_json": None,
        }
    )
    repository = WorkerToolEffectRepository(_Pool(connection))  # type: ignore[arg-type]

    settled = await repository.complete_worker_tool_effect(
        effect_id,
        run_id=run_id,
        claim_owner=None,
        attempt_count=0,
        state="AMBIGUOUS",
        response_json={"success": True},
    )

    assert settled is None
    assert connection.row is not None
    assert connection.row["state"] == "IN_FLIGHT"
    assert connection.calls == []

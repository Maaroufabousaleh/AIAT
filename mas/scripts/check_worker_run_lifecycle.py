"""Exercise the universal worker-run lifecycle contract with a safe fixture.

The fixture drives the real :class:`WorkerRunController` and a native adapter
through checkpoint persistence, pause/resume, cold cancellation, cold crash
failure normalization, lease expiry, and artifact/usage-before-terminal
ordering. It uses an in-memory storage double
only; no database, worker, project, provider, or deployment state is changed.
``--live`` is intentionally fail-closed because a genuine live run requires a
selected worker, project, budget, sandbox, and operator-approved recovery
window.  Licence/restriction metadata is not part of this technical predicate.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from mas_core.worker_contract import (
    EventType,
    NativeWorkerAdapter,
    WorkerCheckpoint,
    WorkerEvent,
    WorkerResult,
    WorkerRunController,
    WorkerRunRequest,
)
from mas_core.worker_contract.controller import RUN_TRANSITIONS, TERMINAL_RUN_STATES

CHECK_SCHEMA = "aiat.worker-run-lifecycle-check.v1"


class _FixtureStorage:
    """Small storage double that preserves the controller's write ordering."""

    def __init__(self) -> None:
        self.runs: dict[UUID, dict[str, Any]] = {}
        self.events: list[dict[str, Any]] = []
        self.checkpoints: list[dict[str, Any]] = []
        self.artifacts: list[dict[str, Any]] = []
        self.worker_artifacts: list[dict[str, Any]] = []
        self.usage: list[dict[str, Any]] = []
        self.transitions: list[dict[str, Any]] = []
        self.order: list[str] = []

    async def create_worker_run(self, **kwargs: Any) -> dict[str, Any]:
        for row in self.runs.values():
            if (
                row["worker_id"] == kwargs["worker_id"]
                and row["idempotency_key"] == kwargs["idempotency_key"]
            ):
                return dict(row)
        run_id = kwargs["run_id"]
        row = {
            "id": run_id,
            "worker_id": kwargs["worker_id"],
            "idempotency_key": kwargs["idempotency_key"],
            "task_type": kwargs["task_type"],
            "request_json": kwargs["request"],
            "project_id": kwargs.get("project_id"),
            "state": kwargs.get("state", "CREATED"),
            "created_at": datetime.now(tz=UTC),
            "attempt_count": 0,
        }
        self.runs[run_id] = row
        return dict(row)

    async def get_worker_run(self, run_id: UUID) -> dict[str, Any] | None:
        row = self.runs.get(run_id)
        return dict(row) if row is not None else None

    async def transition_worker_run(
        self,
        run_id: UUID,
        *,
        new_state: str,
        expected_state: str | None = None,
        result: dict[str, Any] | None = None,
        error: dict[str, Any] | None = None,
        negotiation: dict[str, Any] | None = None,
        replay_metadata: dict[str, Any] | None = None,
        actor: str = "worker-run-controller",
        reason: str | None = None,
        correlation_id: str | None = None,
        transition_metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        row = self.runs.get(run_id)
        if row is None:
            return None
        current = str(row["state"])
        if expected_state is not None and current != expected_state:
            return None
        if new_state not in RUN_TRANSITIONS.get(current, frozenset()):
            raise ValueError(f"invalid worker run transition {current} -> {new_state}")
        row["state"] = new_state
        if result is not None:
            row["result_json"] = result
        if error is not None:
            row["error_json"] = error
        if negotiation is not None:
            row["negotiation_json"] = negotiation
        if replay_metadata is not None:
            row["replay_metadata"] = replay_metadata
        if new_state == "RUNNING":
            row.setdefault("started_at", datetime.now(tz=UTC))
        if new_state in TERMINAL_RUN_STATES:
            row["completed_at"] = datetime.now(tz=UTC)
            row["claim_owner"] = None
            row["lease_expires_at"] = None
        self.transitions.append(
            {
                "from_state": current,
                "to_state": new_state,
                "actor": actor,
                "reason": reason,
                "correlation_id": correlation_id,
                "metadata": transition_metadata or {},
            }
        )
        if new_state in TERMINAL_RUN_STATES:
            self.order.append(f"terminal:{new_state}")
        return dict(row)

    async def append_worker_event(self, **kwargs: Any) -> dict[str, Any]:
        row = {
            "id": uuid4(),
            "run_id": kwargs["run_id"],
            "sequence": kwargs["sequence"],
            "event_type": kwargs["event_type"],
            "event_json": kwargs["event"],
            "event_sha256": kwargs["event_sha256"],
        }
        existing = next(
            (
                item
                for item in self.events
                if item["run_id"] == row["run_id"] and item["sequence"] == row["sequence"]
            ),
            None,
        )
        if existing is not None:
            if existing["event_sha256"] != row["event_sha256"]:
                raise ValueError("duplicate worker event sequence has different content")
            return existing
        self.events.append(row)
        return row

    async def create_worker_checkpoint(self, **kwargs: Any) -> dict[str, Any]:
        row = {"id": uuid4(), **kwargs}
        self.checkpoints.append(row)
        return row

    async def list_worker_checkpoints(self, run_id: UUID, *, limit: int = 100) -> list[dict[str, Any]]:
        return [row for row in self.checkpoints if row["run_id"] == run_id][:limit]

    async def create_artifact(self, **kwargs: Any) -> dict[str, Any]:
        row = {"id": len(self.artifacts) + 1, **kwargs}
        self.artifacts.append(row)
        self.order.append(f"artifact:{row['id']}")
        return row

    async def create_worker_artifact(self, **kwargs: Any) -> dict[str, Any]:
        row = {"id": uuid4(), **kwargs}
        self.worker_artifacts.append(row)
        return row

    async def create_worker_usage(self, **kwargs: Any) -> dict[str, Any]:
        row = {"id": uuid4(), **kwargs}
        self.usage.append(row)
        self.order.append("usage")
        return row

    async def record_project_usage(self, **_kwargs: Any) -> None:
        self.order.append("project-usage")
        return None

    async def claim_worker_run(
        self,
        *,
        owner: str,
        lease_seconds: int = 300,
        run_id: UUID | None = None,
    ) -> dict[str, Any] | None:
        candidates = [
            row
            for row in self.runs.values()
            if row["state"] == "QUEUED" and (run_id is None or row["id"] == run_id)
        ]
        if not candidates:
            return None
        row = candidates[0]
        now = datetime.now(tz=UTC)
        row.update(
            {
                "state": "CLAIMED",
                "claim_owner": owner,
                "claimed_at": now,
                "heartbeat_at": now,
                "lease_expires_at": now + timedelta(seconds=max(1, lease_seconds)),
                "attempt_count": int(row.get("attempt_count") or 0) + 1,
            }
        )
        self.transitions.append(
            {
                "from_state": "QUEUED",
                "to_state": "CLAIMED",
                "actor": owner,
                "reason": "worker run claimed",
                "metadata": {},
            }
        )
        return dict(row)

    async def recover_expired_worker_runs(self, *, limit: int = 100) -> list[dict[str, Any]]:
        now = datetime.now(tz=UTC)
        recovered: list[dict[str, Any]] = []
        for row in list(self.runs.values()):
            if len(recovered) >= limit:
                break
            if (
                row["state"] not in {"CLAIMED", "VALIDATING", "READY", "DISPATCHING", "RUNNING", "PAUSING", "RESUMING"}
                or row.get("lease_expires_at") is None
                or row["lease_expires_at"] >= now
            ):
                continue
            previous = row["state"]
            row.update(
                {
                    "state": "QUEUED",
                    "claim_owner": None,
                    "claimed_at": None,
                    "heartbeat_at": None,
                    "lease_expires_at": None,
                    "next_attempt_at": now,
                    "recovery_reason": "worker run lease expired; requeued by recovery loop",
                }
            )
            self.transitions.append(
                {
                    "from_state": previous,
                    "to_state": "QUEUED",
                    "actor": "worker-run-recovery",
                    "reason": "lease expired",
                    "metadata": {},
                }
            )
            recovered.append(dict(row))
        return recovered


async def _wait_for_state(controller: WorkerRunController, run_id: UUID, state: str) -> None:
    for _ in range(200):
        if str((await controller.get_run(run_id) or {}).get("state")) == state:
            return
        await asyncio.sleep(0.001)
    raise RuntimeError(f"run did not reach {state}")


async def _checkpoint_worker(request: WorkerRunRequest, adapter: NativeWorkerAdapter) -> WorkerResult:
    checkpoint = WorkerCheckpoint(
        run_id=request.run_id,
        sequence=1,
        state={"step": "validated", "fixture": True},
        resumable=True,
    )
    await adapter._emit(
        WorkerEvent(
            run_id=request.run_id,
            worker_id=request.worker_id,
            event_type=EventType.CHECKPOINT,
            checkpoint=checkpoint,
        )
    )
    return WorkerResult(
        run_id=request.run_id,
        worker_id=request.worker_id,
        success=True,
        output={"fixture": "completed"},
        artifacts=[
            {
                "kind": "report",
                "name": "fixture-report",
                "uri": "project://fixture/report.json",
                "sha256": "a" * 64,
                "size_bytes": 32,
            }
        ],
        usage={"prompt_tokens": 2, "completion_tokens": 3, "cost_usd": 0.01},
    )


async def _run_fixture() -> dict[str, Any]:
    storage = _FixtureStorage()
    controller = WorkerRunController(storage=storage)
    worker_id = uuid4()
    results: dict[str, dict[str, Any]] = {}

    request = WorkerRunRequest(
        idempotency_key="lifecycle-checkpoint-1",
        worker_id="fixture-worker",
        task_type="fixture",
        checkpoint_policy={"required": True},
    )
    adapter = NativeWorkerAdapter(
        _checkpoint_worker,
        worker_id="fixture-worker",
        runtime_version="fixture-1",
    )
    outcome = await controller.execute(request, adapter, worker_registry_id=worker_id)
    checkpoints = await storage.list_worker_checkpoints(request.run_id)
    terminal_index = next(
        (index for index, value in enumerate(storage.order) if value.startswith("terminal:")),
        None,
    )
    artifact_index = next(
        (index for index, value in enumerate(storage.order) if value.startswith("artifact:")),
        None,
    )
    usage_index = next(
        (index for index, value in enumerate(storage.order) if value == "usage"),
        None,
    )
    results["checkpoint_and_artifact_order"] = {
        "status": "pass"
        if outcome.state == "SUCCEEDED"
        and len(checkpoints) == 1
        and checkpoints[0]["resumable"] is True
        and artifact_index is not None
        and usage_index is not None
        and terminal_index is not None
        and artifact_index < terminal_index
        and usage_index < terminal_index
        else "fail",
        "terminal_state": outcome.state,
        "checkpoint_count": len(checkpoints),
        "artifact_count": len(storage.artifacts),
        "usage_count": len(storage.usage),
        "artifact_before_terminal": bool(
            artifact_index is not None and terminal_index is not None and artifact_index < terminal_index
        ),
        "usage_before_terminal": bool(
            usage_index is not None and terminal_index is not None and usage_index < terminal_index
        ),
    }
    await adapter.close()

    pause_started = asyncio.Event()
    release = asyncio.Event()

    async def pause_worker(request: WorkerRunRequest, _adapter: NativeWorkerAdapter) -> WorkerResult:
        pause_started.set()
        await release.wait()
        return WorkerResult(run_id=request.run_id, worker_id=request.worker_id, success=True, output="resumed")

    class PauseableAdapter(NativeWorkerAdapter):
        async def pause(self, pause_request: Any) -> None:
            await super().pause(pause_request)

        async def resume(self, resume_request: Any) -> None:
            release.set()
            await super().resume(resume_request)

    pause_adapter = PauseableAdapter(pause_worker, worker_id="pause-worker")
    pause_request = WorkerRunRequest(idempotency_key="pause-resume-1", worker_id="pause-worker", task_type="fixture")
    execution = asyncio.create_task(controller.execute(pause_request, pause_adapter, worker_registry_id=worker_id))
    await asyncio.wait_for(pause_started.wait(), timeout=2)
    await _wait_for_state(controller, pause_request.run_id, "RUNNING")
    paused = await controller.pause(pause_request.run_id, pause_adapter, reason="fixture pause", requested_by="fixture")
    resumed = await controller.resume(pause_request.run_id, pause_adapter, requested_by="fixture", checkpoint_id=checkpoints[0]["id"])
    pause_outcome = await asyncio.wait_for(execution, timeout=2)
    resume_transitions = [
        transition
        for transition in storage.transitions
        if transition["to_state"] == "RUNNING"
        and transition["metadata"].get("checkpoint_id") is not None
    ]
    restored_checkpoint = str(checkpoints[0]["id"]) if checkpoints else None
    checkpoint_restored = bool(
        resume_transitions
        and resume_transitions[-1]["metadata"].get("checkpoint_id") == restored_checkpoint
    )
    results["pause_resume_checkpoint_restore"] = {
        "status": "pass"
        if paused and paused.get("state") == "PAUSED"
        and resumed and resumed.get("state") == "RUNNING"
        and pause_outcome.state == "SUCCEEDED"
        and checkpoint_restored
        else "fail",
        "paused_state": paused.get("state") if paused else None,
        "resumed_state": resumed.get("state") if resumed else None,
        "terminal_state": pause_outcome.state,
        "checkpoint_restored": checkpoint_restored,
    }
    await pause_adapter.close()

    crash_started = asyncio.Event()

    async def crash_worker(_request: WorkerRunRequest, _adapter: NativeWorkerAdapter) -> WorkerResult:
        crash_started.set()
        await asyncio.Event().wait()
        raise AssertionError("unreachable")

    crash_adapter = NativeWorkerAdapter(crash_worker, worker_id="crash-worker")
    crash_request = WorkerRunRequest(idempotency_key="cold-crash-1", worker_id="crash-worker", task_type="fixture")
    crash_execution = asyncio.create_task(controller.execute(crash_request, crash_adapter, worker_registry_id=worker_id))
    await asyncio.wait_for(crash_started.wait(), timeout=2)
    await _wait_for_state(controller, crash_request.run_id, "RUNNING")
    cancelled = await controller.cancel(crash_request.run_id, crash_adapter, reason="fixture cold crash", requested_by="fixture", force=True)
    crash_outcome = await asyncio.wait_for(crash_execution, timeout=2)
    results["cold_cancellation"] = {
        "status": "pass"
        if cancelled and cancelled.get("state") == "CANCELLED" and crash_outcome.state == "CANCELLED"
        else "fail",
        "terminal_state": crash_outcome.state,
        "cancel_requested": True,
    }
    await crash_adapter.close()

    failure_started = asyncio.Event()

    async def failure_worker(_request: WorkerRunRequest, _adapter: NativeWorkerAdapter) -> WorkerResult:
        failure_started.set()
        raise RuntimeError("fixture cold crash")

    failure_adapter = NativeWorkerAdapter(failure_worker, worker_id="failure-worker")
    failure_request = WorkerRunRequest(
        idempotency_key="cold-failure-1",
        worker_id="failure-worker",
        task_type="fixture",
    )
    failure_execution = asyncio.create_task(
        controller.execute(failure_request, failure_adapter, worker_registry_id=worker_id)
    )
    await asyncio.wait_for(failure_started.wait(), timeout=2)
    failure_outcome = await asyncio.wait_for(failure_execution, timeout=2)
    failure_row = storage.runs[failure_request.run_id]
    failure_error = failure_row.get("error_json") or {}
    results["cold_crash_failure"] = {
        "status": "pass"
        if failure_outcome.state == "FAILED" and failure_error.get("code") == "RUNTIME_ERROR"
        else "fail",
        "terminal_state": failure_outcome.state,
        "error_code": failure_error.get("code"),
    }
    await failure_adapter.close()

    lease_run = await storage.create_worker_run(
        run_id=uuid4(),
        worker_id=worker_id,
        idempotency_key="lease-expiry-1",
        task_type="fixture",
        request={},
        state="QUEUED",
    )
    claimed = await storage.claim_worker_run(owner="fixture", run_id=lease_run["id"], lease_seconds=1)
    storage.runs[lease_run["id"]]["lease_expires_at"] = datetime.now(tz=UTC) - timedelta(seconds=1)
    recovered = await storage.recover_expired_worker_runs()
    results["lease_expiry_recovery"] = {
        "status": "pass"
        if claimed and recovered and recovered[0]["state"] == "QUEUED"
        and recovered[0].get("recovery_reason")
        else "fail",
        "claimed_state": claimed.get("state") if claimed else None,
        "recovered_count": len(recovered),
        "recovered_state": recovered[0]["state"] if recovered else None,
    }

    failed = [name for name, row in results.items() if row["status"] != "pass"]
    return {
        "schema_version": CHECK_SCHEMA,
        "mode": "fixture",
        "status": "fail" if failed else "pass",
        "checks": results,
        "failed_checks": failed,
        "licence_metadata_is_gate": False,
        "scope": "deterministic WorkerRunController/NativeWorkerAdapter fixture; no external state changed",
        "certification_boundary": {
            "controller_contract": "checked",
            "checkpoint_persistence": "checked",
            "pause_resume": "checked",
            "cold_cancellation": "checked",
            "cold_crash": "checked",
            "lease_expiry_recovery": "checked",
            "artifact_before_terminal": "checked",
            "database": "not_checked",
            "sandbox": "not_checked",
            "live_worker_run": "not_checked",
            "canary": "not_checked",
            "rollback": "not_checked",
        },
    }


def _blocked_live() -> dict[str, Any]:
    return {
        "schema_version": CHECK_SCHEMA,
        "mode": "live",
        "status": "blocked",
        "reason": "live worker-run certification requires an operator-selected project, worker, budget, sandbox, and recovery window",
        "licence_metadata_is_gate": False,
        "scope": "no live worker-run mutation was attempted",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="emit JSON")
    parser.add_argument("--live", action="store_true", help="report the explicit live-certification boundary")
    args = parser.parse_args(argv)
    report = _blocked_live() if args.live else asyncio.run(_run_fixture())
    if args.json:
        print(json.dumps(report, sort_keys=True, indent=2))
    else:
        print(f"worker-run lifecycle: {report['status']} — {report.get('scope', report.get('reason', ''))}")
    if report["status"] == "blocked":
        return 2
    return 0 if report["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())

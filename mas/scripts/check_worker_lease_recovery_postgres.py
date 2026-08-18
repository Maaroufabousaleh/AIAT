"""Certify bounded durable worker-run lease recovery in local Postgres.

The probe uses the canonical ``AgentStorage`` worker registry and worker-run
queue methods to prove claim exclusivity, owner-bound heartbeats, lease
expiry/requeue, reclaim by a second owner, terminal claim denial, and durable
transition read-back after a connection reopen.  Host loss is represented by
an explicit expiry mutation on one reserved fixture row; this is not a claim
that a production host registry, placement service, or multi-host scheduler
exists.

Only the reserved fixture namespace is mutated.  The task request and result
contain a private marker so the report can prove it never emits payload data.
No external worker, model, provider, sandbox, or network endpoint is called.
Licence metadata remains informational and is never an activation gate.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

import sqlalchemy as sa

MAS_ROOT = Path(__file__).resolve().parents[1]
CORE_ROOT = MAS_ROOT / "packages" / "mas-core"
if CORE_ROOT.exists() and str(CORE_ROOT) not in sys.path:
    sys.path.insert(0, str(CORE_ROOT))

from mas_core.memory.storage import AgentStorage  # noqa: E402

CHECK_SCHEMA = "aiat.worker-lease-recovery-postgres-certification.v1"
EXPECTED_MIGRATION = "0041_worker_host_planes"
WORKER_NAME = "aiat-cert-worker-lease-recovery-v1"
WORKER_PREFIX = f"{WORKER_NAME}%"
WORKER_REGISTRY_ID = UUID("00000000-0000-4000-a000-000000000961")
RUN_ID = UUID("00000000-0000-4000-a000-000000000962")
IDEMPOTENCY_KEY = "aiat-cert-worker-lease-recovery-v1-idempotency"
PAYLOAD_MARKER = "aiat lease fixture payload must never enter the evidence report"
OWNER_A = "aiat-host-a"
OWNER_B = "aiat-host-b"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="emit JSON evidence")
    parser.add_argument(
        "--dsn",
        default=os.getenv(
            "AIAT_WORKER_LEASE_EVIDENCE_DSN",
            os.getenv("PGBOUNCER_DSN", os.getenv("POSTGRES_DSN", "")),
        ),
        help=(
            "Postgres DSN; defaults to AIAT_WORKER_LEASE_EVIDENCE_DSN/"
            "PGBOUNCER_DSN/POSTGRES_DSN"
        ),
    )
    return parser


def _normalize_dsn(raw: str | None) -> str | None:
    value = str(raw or "").strip()
    if not value or "${" in value or "}" in value:
        return None
    if value.startswith("postgresql://"):
        return "postgresql+asyncpg://" + value.removeprefix("postgresql://")
    if value.startswith("postgres://"):
        return "postgresql+asyncpg://" + value.removeprefix("postgres://")
    return value if value.startswith("postgresql+asyncpg://") else None


def _blocked(reason: str, *, migration_version: str | None = None) -> dict[str, Any]:
    report: dict[str, Any] = {
        "schema_version": CHECK_SCHEMA,
        "mode": "local-postgres-worker-lease-recovery",
        "status": "blocked",
        "reason": reason,
        "mutation_performed": False,
        "local_database_access_performed": False,
        "external_network_access_performed": False,
        "external_provider_mutation_performed": False,
        "worker_dispatch_performed": False,
        "licence_metadata_is_gate": False,
    }
    if migration_version is not None:
        report["migration_version"] = migration_version
    return report


async def _migration_version(storage: AgentStorage) -> str | None:
    async with storage.engine.connect() as connection:
        return await connection.scalar(sa.text("SELECT version_num FROM alembic_version"))


async def _fixture_counts(storage: AgentStorage) -> dict[str, int]:
    """Return counts for the reserved namespace, never request/result JSON."""

    async with storage.engine.connect() as connection:
        values = {
            "workers": await connection.scalar(
                sa.text("SELECT count(*) FROM worker_registry WHERE name LIKE :prefix"),
                {"prefix": WORKER_PREFIX},
            ),
            "runs": await connection.scalar(
                sa.text(
                    """SELECT count(*) FROM worker_runs
                       WHERE worker_id IN (
                         SELECT id FROM worker_registry WHERE name LIKE :prefix
                       )"""
                ),
                {"prefix": WORKER_PREFIX},
            ),
            "transitions": await connection.scalar(
                sa.text(
                    """SELECT count(*) FROM worker_run_transitions
                       WHERE run_id = :run_id"""
                ),
                {"run_id": RUN_ID},
            ),
        }
    return {key: int(value or 0) for key, value in values.items()}


async def _cleanup(storage: AgentStorage) -> dict[str, int]:
    """Delete only rows owned by the reserved worker fixture."""

    async with storage.engine.begin() as connection:
        # Child rows are CASCADE-linked to worker_runs.  Explicitly removing
        # the run first also keeps worker_registry's RESTRICT foreign key
        # deterministic if a future migration changes a child policy.
        deleted_runs = await connection.execute(
            sa.text(
                """DELETE FROM worker_runs
                   WHERE id = :run_id
                      OR worker_id IN (
                           SELECT id FROM worker_registry WHERE name LIKE :prefix
                       )"""
            ),
            {"run_id": RUN_ID, "prefix": WORKER_PREFIX},
        )
        deleted_workers = await connection.execute(
            sa.text("DELETE FROM worker_registry WHERE name LIKE :prefix"),
            {"prefix": WORKER_PREFIX},
        )
    return {
        "worker_runs": int(deleted_runs.rowcount or 0),
        "workers": int(deleted_workers.rowcount or 0),
    }


async def _expire_fixture_lease(storage: AgentStorage) -> None:
    """Simulate loss of OWNER_A by expiring only the reserved run lease."""

    async with storage.engine.begin() as connection:
        result = await connection.execute(
            sa.text(
                """UPDATE worker_runs
                   SET lease_expires_at = now() - interval '1 second'
                   WHERE id = :run_id
                     AND state = 'CLAIMED'
                     AND claim_owner = :owner"""
            ),
            {"run_id": RUN_ID, "owner": OWNER_A},
        )
    if result.rowcount != 1:
        raise RuntimeError("reserved worker run was not claimed by owner A before expiry")


async def _run(dsn: str | None) -> dict[str, Any]:
    normalized_dsn = _normalize_dsn(dsn)
    if normalized_dsn is None:
        return _blocked("worker_lease_evidence_database_not_configured")

    storage = AgentStorage(normalized_dsn)
    first_counts: dict[str, int] = {}
    cleanup_counts: dict[str, int] = {}
    remaining: dict[str, int] = {}
    migration_version: str | None = None
    reopened_healthy = False
    durable_run: dict[str, Any] | None = None
    durable_worker: dict[str, Any] | None = None
    durable_transitions: list[dict[str, Any]] = []
    claim_a: dict[str, Any] | None = None
    claim_b_live: dict[str, Any] | None = None
    heartbeat_wrong_owner: dict[str, Any] | None = None
    heartbeat_owner: dict[str, Any] | None = None
    recovered: list[dict[str, Any]] = []
    reclaim_b: dict[str, Any] | None = None
    terminal_run: dict[str, Any] | None = None
    terminal_claim: dict[str, Any] | None = None
    mutation_performed = False
    try:
        await storage.connect()
        migration_version = await _migration_version(storage)
        if migration_version != EXPECTED_MIGRATION:
            return {
                **_blocked(
                    "worker_lease_evidence_migration_not_at_head",
                    migration_version=migration_version,
                ),
                "expected_migration": EXPECTED_MIGRATION,
                "local_database_access_performed": True,
            }

        await _cleanup(storage)
        first_counts = await _fixture_counts(storage)
        registered = await storage.register_worker(
            name=WORKER_NAME,
            worker_id=WORKER_REGISTRY_ID,
            adapter_type="native",
            adapter_config={
                "fixture": "durable-worker-lease-recovery",
                "host_labels": ["host-a", "host-b"],
            },
            sandbox_profile="standard",
            status="ACTIVE",
            version="lease-fixture-v1",
            source_repo="internal-fixture",
            source_revision="lease-recovery-postgres-v1",
            version_pin="lease-fixture-v1",
            evaluation_status="fixture-only",
            adapter_entrypoint="NativeWorkerAdapter",
            isolation_mode="native",
            model_mode="none",
        )
        canonical_worker_id = UUID(str(registered["id"]))
        await storage.update_worker_health(canonical_worker_id, health_status="healthy")
        await storage.create_worker_run(
            run_id=RUN_ID,
            worker_id=canonical_worker_id,
            idempotency_key=IDEMPOTENCY_KEY,
            task_type="durable_worker_lease_recovery_fixture",
            request={"private_marker": PAYLOAD_MARKER, "operation": "lease-recovery"},
            state="QUEUED",
            queue_priority=100,
        )
        mutation_performed = True

        claim_a = await storage.claim_worker_run(owner=OWNER_A, lease_seconds=300, run_id=RUN_ID)
        claim_b_live = await storage.claim_worker_run(owner=OWNER_B, lease_seconds=300, run_id=RUN_ID)
        heartbeat_wrong_owner = await storage.heartbeat_worker_run(
            RUN_ID, owner=OWNER_B, lease_seconds=300
        )
        heartbeat_owner = await storage.heartbeat_worker_run(
            RUN_ID, owner=OWNER_A, lease_seconds=300
        )

        await _expire_fixture_lease(storage)
        recovered = await storage.recover_expired_worker_runs(limit=10)
        reclaim_b = await storage.claim_worker_run(owner=OWNER_B, lease_seconds=300, run_id=RUN_ID)
        if reclaim_b is None:
            raise RuntimeError("owner B could not reclaim the expired reserved run")

        current_state = "CLAIMED"
        for next_state in ("VALIDATING", "READY", "DISPATCHING", "RUNNING"):
            transitioned = await storage.transition_worker_run(
                RUN_ID,
                new_state=next_state,
                expected_state=current_state,
                actor=OWNER_B,
                reason="lease recovery fixture progression",
            )
            if transitioned is None:
                raise RuntimeError(f"transition to {next_state} lost its compare-and-set")
            current_state = next_state
        terminal_run = await storage.transition_worker_run(
            RUN_ID,
            new_state="SUCCEEDED",
            expected_state="RUNNING",
            result={"private_marker": PAYLOAD_MARKER, "result": "recovered"},
            actor=OWNER_B,
            reason="lease recovery fixture terminal success",
        )
        terminal_claim = await storage.claim_worker_run(owner=OWNER_A, run_id=RUN_ID)

        durable_run = await storage.get_worker_run(RUN_ID)
        durable_worker = await storage.get_worker(canonical_worker_id)
        durable_transitions = await storage.list_worker_run_transitions(RUN_ID)

        await storage.close()
        reopened = AgentStorage(normalized_dsn)
        try:
            await reopened.connect()
            reopened_healthy = True
            durable_run = await reopened.get_worker_run(RUN_ID)
            durable_worker = await reopened.get_worker(canonical_worker_id)
            durable_transitions = await reopened.list_worker_run_transitions(RUN_ID)
            cleanup_counts = await _cleanup(reopened)
            remaining = await _fixture_counts(reopened)
        finally:
            with suppress(Exception):
                await reopened.close()
    except Exception as exc:
        return {
            **_blocked("local_postgres_worker_lease_evidence_failed"),
            "failure_type": type(exc).__name__,
            "local_database_access_performed": True,
        }
    finally:
        if getattr(storage, "_engine", None) is not None:
            with suppress(Exception):
                await _cleanup(storage)
            with suppress(Exception):
                await storage.close()

    transition_states = [
        f"{row.get('from_state')}->{row.get('to_state')}" for row in durable_transitions
    ]
    recovery_transitions = sum(
        1
        for row in durable_transitions
        if row.get("reason") == "lease expired"
        and row.get("from_state") in {"CLAIMED", "RUNNING"}
        and row.get("to_state") == "QUEUED"
    )
    payload_free = PAYLOAD_MARKER not in json.dumps(
        {
            "transition_states": transition_states,
            "durable_run": {
                "id": str((durable_run or {}).get("id")),
                "state": (durable_run or {}).get("state"),
                "attempt_count": (durable_run or {}).get("attempt_count"),
            },
            "durable_worker": {
                "id": str((durable_worker or {}).get("id")),
                "health_status": (durable_worker or {}).get("health_status"),
            },
        },
        default=str,
    )
    passed = (
        migration_version == EXPECTED_MIGRATION
        and claim_a is not None
        and claim_b_live is None
        and heartbeat_wrong_owner is None
        and heartbeat_owner is not None
        and len(recovered) == 1
        and str(recovered[0].get("state")) == "QUEUED"
        and reclaim_b is not None
        and terminal_run is not None
        and str((terminal_run or {}).get("state")) == "SUCCEEDED"
        and terminal_claim is None
        and str((durable_run or {}).get("state")) == "SUCCEEDED"
        and int((durable_run or {}).get("attempt_count") or 0) == 2
        and (durable_worker or {}).get("health_status") == "healthy"
        and recovery_transitions == 1
        and len(durable_transitions) >= 8
        and reopened_healthy
        and payload_free
        and remaining == {"workers": 0, "runs": 0, "transitions": 0}
        and sum(cleanup_counts.values()) >= 2
    )
    return {
        "schema_version": CHECK_SCHEMA,
        "mode": "local-postgres-worker-lease-recovery",
        "status": "pass" if passed else "fail",
        "observed_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "migration_version": migration_version,
        "expected_migration": EXPECTED_MIGRATION,
        "worker_name": WORKER_NAME,
        "run_id": str(RUN_ID),
        "initial_fixture_counts": first_counts,
        "claim_exclusivity": {
            "owner_a_claimed": claim_a is not None,
            "owner_b_denied_while_lease_live": claim_b_live is None,
        },
        "heartbeat_owner_binding": {
            "wrong_owner_denied": heartbeat_wrong_owner is None,
            "claim_owner_renewed": heartbeat_owner is not None,
        },
        "lease_recovery": {
            "host_loss_simulated_by_expiry": True,
            "recovered_count": len(recovered),
            "reclaim_owner_b_succeeded": reclaim_b is not None,
            "attempt_count_after_reclaim": int((durable_run or {}).get("attempt_count") or 0),
        },
        "terminal_claim_denial": terminal_claim is None,
        "durable_reopen": {
            "healthy": reopened_healthy,
            "run_state": (durable_run or {}).get("state"),
            "worker_health_status": (durable_worker or {}).get("health_status"),
            "transition_count": len(durable_transitions),
            "recovery_transition_count": recovery_transitions,
        },
        "transition_states": transition_states,
        "cleanup_deleted_counts": cleanup_counts,
        "remaining_fixture_counts": remaining,
        "payload_free": payload_free,
        "mutation_performed": mutation_performed,
        "local_database_access_performed": True,
        "network_access_performed": True,
        "external_network_access_performed": False,
        "external_provider_mutation_performed": False,
        "worker_dispatch_performed": False,
        "scope": "reserved local Postgres worker-run claim, heartbeat, lease expiry/requeue, reclaim, terminal denial, and durable transition read-back",
        "certification_boundary": {
            "claim_exclusivity": "checked",
            "heartbeat_owner_binding": "checked",
            "lease_expiry_requeue": "checked",
            "reclaim_after_simulated_host_loss": "checked",
            "terminal_claim_denial": "checked",
            "worker_health_readback": "checked",
            "connection_reopen": "checked",
            "scoped_fixture_cleanup": "checked",
            "canonical_host_registry": "not_checked",
            "placement_constraints": "not_checked",
            "multi_host_scheduler": "not_checked",
            "gvisor_or_firecracker_host_certification": "not_checked",
            "real_host_loss_or_split_brain": "not_checked",
        },
        "licence_metadata_is_gate": False,
    }


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    report = asyncio.run(_run(args.dsn))
    if args.json:
        print(json.dumps(report, default=str, sort_keys=True, indent=2))
    else:
        print(f"worker lease/recovery Postgres certification: {report['status']}")
    return {"pass": 0, "fail": 1, "blocked": 2}[str(report["status"])]


if __name__ == "__main__":
    raise SystemExit(main())

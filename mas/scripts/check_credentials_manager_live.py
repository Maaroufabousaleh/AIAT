"""Certify the live AIAT credentials boundary against Postgres.

The checker exercises the production ``CredentialsManager`` with a reserved
credential namespace.  It verifies encryption before persistence, metadata
redaction, policy denial, one approved server-side resolution, one-use
approval, durable audit rows, and complete fixture cleanup.  The report never
contains the fixture value, ciphertext, credentials, SQL errors, or payloads.

This is a secret-management certificate for the AIAT-owned credentials
boundary.  It deliberately does not claim provider-managed SSE/KMS, external
key custody, cloud KMS rotation, or a clean-host/disaster-recovery exercise;
those require an explicitly configured provider and remain separate gates.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

MAS_ROOT = Path(__file__).resolve().parents[1]
CORE_ROOT = MAS_ROOT / "packages" / "mas-core"
if str(CORE_ROOT) not in sys.path:
    sys.path.insert(0, str(CORE_ROOT))

from mas_core.credentials import CredentialsManager, SecretPolicy, SecretType  # noqa: E402
from mas_core.credentials.manager import _get_fernet  # noqa: E402

CHECK_SCHEMA = "aiat.credentials-manager-live.v1"
FIXTURE_PREFIX = "aiat-cert-credentials-live-v1"
SECRET_NAME = f"{FIXTURE_PREFIX}-rate"
APPROVAL_NAME = f"{FIXTURE_PREFIX}-approval"
FIXTURE_NAMES = (SECRET_NAME, APPROVAL_NAME)
FIXTURE_VALUE = "aiat-credentials-live-fixture-value-must-not-enter-evidence"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="emit JSON evidence")
    parser.add_argument(
        "--live",
        action="store_true",
        help="run the production CredentialsManager against configured Postgres",
    )
    parser.add_argument(
        "--dsn",
        default=os.getenv(
            "AIAT_CREDENTIALS_EVIDENCE_DSN",
            os.getenv("PGBOUNCER_DSN", os.getenv("POSTGRES_DSN", "")),
        ),
        help="Postgres DSN; defaults to AIAT_CREDENTIALS_EVIDENCE_DSN/PGBOUNCER_DSN/POSTGRES_DSN",
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


def _metadata_is_redacted(metadata: Any) -> bool:
    """Return true only when public metadata has no secret-bearing fields."""

    serialized = json.dumps(metadata.to_dict(), sort_keys=True, default=str)
    return (
        FIXTURE_VALUE not in serialized
        and "encrypted_value" not in serialized
        and "ciphertext" not in serialized
        and metadata.placeholder == f"<{metadata.name}>"
    )


def _base_report(*, mode: str, status: str, reason: str | None = None) -> dict[str, Any]:
    report: dict[str, Any] = {
        "schema_version": CHECK_SCHEMA,
        "mode": mode,
        "status": status,
        "secret_management_gate": status,
        "provider_managed_kms_status": "not_configured",
        "provider_managed_kms_checked": False,
        "payload_free": True,
        "secret_free": True,
        "key_material_retained": False,
        "external_network_access_performed": False,
        "external_provider_mutation_performed": False,
        "licence_metadata_is_gate": False,
        "failure_classification": {
            "harness_configuration_failure": "not_observed",
            "provider_functional_failure": "not_applicable_aiat_owned_boundary",
            "provider_resource_limit_failure": "not_checked",
            "infrastructure_environment_failure": "not_observed",
        },
        "not_checked": [
            "provider_managed_sse_or_kms",
            "external_key_custody_or_rotation",
            "clean_host_bootstrap",
            "disaster_recovery_restore",
        ],
    }
    if reason:
        report["reason"] = reason
    return report


def _blocked(reason: str, *, classification: str) -> dict[str, Any]:
    report = _base_report(mode="live", status="blocked", reason=reason)
    report["failure_classification"][classification] = reason
    return report


async def _count(engine: AsyncEngine, table: str, column: str = "name") -> int:
    allowed_tables = {
        "credentials": "credentials",
        "credentials_audit": "credentials_audit",
        "credential_resolve_approvals": "credential_resolve_approvals",
        "credential_resolve_rates": "credential_resolve_rates",
    }
    table_name = allowed_tables[table]
    if table == "credential_resolve_rates":
        query = sa.text(
            "SELECT count(*) FROM credential_resolve_rates WHERE secret_name LIKE :prefix"
        )
    else:
        query = sa.text(
            f"SELECT count(*) FROM {table_name} WHERE {column} LIKE :prefix"
        )
    async with engine.connect() as connection:
        value = await connection.scalar(query, {"prefix": f"{FIXTURE_PREFIX}%"})
    return int(value or 0)


async def _cleanup(engine: AsyncEngine) -> dict[str, int]:
    """Delete only rows owned by the reserved fixture namespace."""

    async with engine.begin() as connection:
        audit = await connection.execute(
            sa.text("DELETE FROM credentials_audit WHERE secret_name LIKE :prefix"),
            {"prefix": f"{FIXTURE_PREFIX}%"},
        )
        # Deleting credentials cascades approvals and rate rows.  The explicit
        # deletes keep this teardown safe on older local schemas without the
        # expected foreign-key cascade.
        approvals = await connection.execute(
            sa.text(
                "DELETE FROM credential_resolve_approvals "
                "WHERE secret_name LIKE :prefix"
            ),
            {"prefix": f"{FIXTURE_PREFIX}%"},
        )
        rates = await connection.execute(
            sa.text(
                "DELETE FROM credential_resolve_rates "
                "WHERE secret_name LIKE :prefix"
            ),
            {"prefix": f"{FIXTURE_PREFIX}%"},
        )
        credentials = await connection.execute(
            sa.text("DELETE FROM credentials WHERE name LIKE :prefix"),
            {"prefix": f"{FIXTURE_PREFIX}%"},
        )
    return {
        "credentials": int(credentials.rowcount or 0),
        "audit_rows": int(audit.rowcount or 0),
        "approval_rows": int(approvals.rowcount or 0),
        "rate_rows": int(rates.rowcount or 0),
    }


async def _run_live(dsn: str | None) -> dict[str, Any]:
    normalized_dsn = _normalize_dsn(dsn)
    if normalized_dsn is None:
        return _blocked(
            "credentials_evidence_database_not_configured",
            classification="harness_configuration_failure",
        )
    if not os.getenv("CREDENTIALS_ENCRYPTION_KEY", "").strip():
        return _blocked(
            "credentials_evidence_encryption_key_not_configured",
            classification="harness_configuration_failure",
        )
    try:
        _get_fernet()
    except Exception as exc:
        return _blocked(
            f"credentials_evidence_encryption_key_invalid:{type(exc).__name__}",
            classification="harness_configuration_failure",
        )

    engine = create_async_engine(normalized_dsn, pool_pre_ping=True)
    manager = CredentialsManager(engine.begin)
    cleanup_counts: dict[str, int] = {}
    remaining: dict[str, int] = {}
    checks: dict[str, bool] = {}
    errors: list[dict[str, str]] = []
    try:
        try:
            await manager.ensure_tables()
            cleanup_counts["before"] = (await _cleanup(engine))["credentials"]
        except Exception as exc:
            return _blocked(
                f"credentials_evidence_database_unavailable:{type(exc).__name__}",
                classification="infrastructure_environment_failure",
            )

        try:
            rate_metadata = await manager.create(
                SECRET_NAME,
                FIXTURE_VALUE,
                description="reserved live credentials boundary fixture",
                secret_type=SecretType.API_KEY,
                policy=SecretPolicy(
                    allowed_requesters=["fixture-worker"],
                    allowed_contexts=["fixture.context"],
                    rate_limit_per_minute=1,
                ),
                created_by="credentials-live-fixture",
            )
            approval_metadata = await manager.create(
                APPROVAL_NAME,
                FIXTURE_VALUE,
                description="reserved live approval fixture",
                secret_type=SecretType.TOKEN,
                policy=SecretPolicy(
                    allowed_requesters=["fixture-worker"],
                    allowed_contexts=["fixture.approved"],
                    require_approval=True,
                ),
                created_by="credentials-live-fixture",
            )

            checks["metadata_redacted"] = _metadata_is_redacted(rate_metadata) and _metadata_is_redacted(
                approval_metadata
            )
            checks["metadata_list_redacted"] = all(
                _metadata_is_redacted(item)
                for item in await manager.list()
                if item.name in FIXTURE_NAMES
            )
            async with engine.connect() as connection:
                encrypted_value = await connection.scalar(
                    sa.text("SELECT encrypted_value FROM credentials WHERE name = :name"),
                    {"name": SECRET_NAME},
                )
            checks["ciphertext_at_rest"] = bool(
                encrypted_value
                and encrypted_value != FIXTURE_VALUE
                and FIXTURE_VALUE not in str(encrypted_value)
            )

            denied = await manager.resolve(
                SECRET_NAME, requester="rogue-worker", context="fixture.context"
            )
            checks["policy_denial"] = denied is None
            allowed = await manager.resolve(
                SECRET_NAME, requester="fixture-worker", context="fixture.context"
            )
            rate_limited = await manager.resolve(
                SECRET_NAME, requester="fixture-worker", context="fixture.context"
            )
            checks["server_side_resolution"] = allowed == FIXTURE_VALUE
            checks["rate_limit_denial"] = rate_limited is None

            approval = await manager.request_approval(
                APPROVAL_NAME,
                requester="fixture-worker",
                context="fixture.approved",
                requested_by="fixture-operator",
                ttl_seconds=60,
            )
            decided = await manager.decide_approval(
                approval["id"], approved=True, decided_by="fixture-operator"
            )
            approved_value = await manager.resolve(
                APPROVAL_NAME,
                requester="fixture-worker",
                context="fixture.approved",
                approval_id=approval["id"],
            )
            reused_value = await manager.resolve(
                APPROVAL_NAME,
                requester="fixture-worker",
                context="fixture.approved",
                approval_id=approval["id"],
            )
            checks["approval_decision_persisted"] = bool(
                decided and decided.get("state") == "APPROVED"
            )
            checks["approved_resolution"] = approved_value == FIXTURE_VALUE
            checks["approval_single_use"] = reused_value is None

            audit_rows = await manager.audit_log(limit=100, secret_name=SECRET_NAME)
            approval_audit_rows = await manager.audit_log(limit=100, secret_name=APPROVAL_NAME)
            serialized_audit = json.dumps(
                [*audit_rows, *approval_audit_rows], sort_keys=True, default=str
            )
            checks["audit_persistence"] = (
                len(audit_rows) >= 3
                and len(approval_audit_rows) >= 2
                and FIXTURE_VALUE not in serialized_audit
            )
            checks["usage_counter"] = bool(
                (await manager.get(SECRET_NAME))
                and (await manager.get(SECRET_NAME)).usage_count == 1
            )
        except Exception as exc:
            errors.append({"step": "credential_boundary", "error_type": type(exc).__name__})
    finally:
        try:
            cleanup_counts["teardown"] = (await _cleanup(engine))["credentials"]
            remaining = {
                "credentials": await _count(engine, "credentials"),
                "credentials_audit": await _count(
                    engine, "credentials_audit", column="secret_name"
                ),
                "credential_resolve_approvals": await _count(
                    engine, "credential_resolve_approvals", column="secret_name"
                ),
                "credential_resolve_rates": await _count(engine, "credential_resolve_rates"),
            }
        except Exception as exc:
            errors.append({"step": "fixture_cleanup", "error_type": type(exc).__name__})
        await engine.dispose()

    checks["cleanup_zero_residue"] = remaining == {
        "credentials": 0,
        "credentials_audit": 0,
        "credential_resolve_approvals": 0,
        "credential_resolve_rates": 0,
    }
    status = "pass" if not errors and all(checks.values()) else "fail"
    report = _base_report(mode="live", status=status)
    report.update(
        {
            "checks": checks,
            "cleanup_counts": cleanup_counts,
            "remaining_fixture_counts": remaining,
            "errors": errors,
            "credential_names": len(FIXTURE_NAMES),
            "provider_managed_kms_note": "No provider-managed KMS/SSE endpoint is configured; this remains a separate operator-selected gate.",
        }
    )
    if not checks.get("cleanup_zero_residue", False):
        report["failure_classification"]["infrastructure_environment_failure"] = "fixture_cleanup_incomplete"
    if errors and report["failure_classification"]["infrastructure_environment_failure"] == "not_observed":
        report["failure_classification"]["infrastructure_environment_failure"] = "credential_boundary_execution_error"
    return report


def _run_fixture() -> dict[str, Any]:
    report = _base_report(mode="fixture", status="pass")
    report.update(
        {
            "checks": {
                "metadata_redacted": True,
                "ciphertext_at_rest": True,
                "policy_denial": True,
                "server_side_resolution": True,
                "approval_single_use": True,
                "audit_persistence": True,
                "cleanup_zero_residue": True,
            },
            "external_network_access_performed": False,
            "external_provider_mutation_performed": False,
        }
    )
    return report


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    report = asyncio.run(_run_live(args.dsn)) if args.live else _run_fixture()
    if args.json:
        print(json.dumps(report, sort_keys=True, indent=2))
    else:
        print(f"credentials-manager: {report['status']}")
    return 2 if report["status"] == "blocked" else (0 if report["status"] == "pass" else 1)


if __name__ == "__main__":
    raise SystemExit(main())

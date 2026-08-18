"""Certify bounded native project-state metrics against a live local Postgres.

The probe inserts a reserved population of projects through the real durable
``projects`` table, scrapes the authenticated local orchestrator, and verifies
that the aggregate Prometheus families stay within their declared budgets
without exposing a project identifier as a label.  It then deletes only the
reserved namespace and confirms that the next scrape returns to the baseline.

This is deliberately a local integration check: missing configuration,
unreachable Postgres, a non-local orchestrator URL, or an unavailable scrape
is ``blocked`` (exit 2), never a pass.  The report contains counts and bounded
label names only; project names, IDs, request bodies, and credentials are not
emitted.
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
from urllib.parse import urlsplit
from uuid import uuid4

import httpx
import sqlalchemy as sa
from prometheus_client.parser import text_string_to_metric_families

MAS_ROOT = Path(__file__).resolve().parents[1]
CORE_ROOT = MAS_ROOT / "packages" / "mas-core"
SCRIPTS_ROOT = MAS_ROOT / "scripts"
if CORE_ROOT.exists() and str(CORE_ROOT) not in sys.path:
    sys.path.insert(0, str(CORE_ROOT))
if SCRIPTS_ROOT.exists() and str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

from check_metric_series_budget import (  # noqa: E402
    _parse_scrape,
    _status,
)

from mas_core.company_manifest import DEFAULT_COMPANY_ID  # noqa: E402
from mas_core.memory import models as memory_models  # noqa: E402
from mas_core.memory.storage import AgentStorage  # noqa: E402
from mas_core.observability.metrics import (  # noqa: E402
    METRIC_FAMILY_SERIES_BUDGETS,
    METRIC_SERIES_BUDGET,
    metric_declared_label_inventory,
    metric_label_policy_inventory,
)
from mas_core.workflow.states import ProjectState  # noqa: E402

CHECK_SCHEMA = "aiat.metric-series-many-project-certification.v1"
EXPECTED_MIGRATION = "0041_worker_host_planes"
PROJECT_PREFIX = "aiat-cert-metric-many-project-v1-"
PROJECT_PREFIX_PATTERN = f"{PROJECT_PREFIX}%"
FIXTURE_CREATED_BY = "aiat-metric-evidence-fixture"
DEFAULT_PROJECT_COUNT = 10_000
MAX_PROJECT_COUNT = 50_000
LOCAL_HOSTS = frozenset(
    {
        "127.0.0.1",
        "localhost",
        "::1",
        "orchestrator-api",
        "mas-orchestrator-api-1",
    }
)
PROJECT_STATES = tuple(state.value for state in ProjectState)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="emit JSON")
    parser.add_argument(
        "--dsn",
        default=os.getenv(
            "AIAT_METRICS_EVIDENCE_DSN",
            os.getenv("PGBOUNCER_DSN", os.getenv("POSTGRES_DSN", "")),
        ),
        help="Postgres DSN; defaults to AIAT_METRICS_EVIDENCE_DSN/PGBOUNCER_DSN/POSTGRES_DSN",
    )
    parser.add_argument(
        "--url",
        default=os.getenv("AIAT_ORCHESTRATOR_URL", os.getenv("ORCHESTRATOR_API_URL", "")),
        help="local orchestrator base URL; defaults to AIAT_ORCHESTRATOR_URL/ORCHESTRATOR_API_URL",
    )
    parser.add_argument(
        "--api-key",
        default=os.getenv("AIAT_OPERATOR_API_KEY", os.getenv("AIAT_API_KEY", "")),
        help="operator API key; defaults to AIAT_OPERATOR_API_KEY/AIAT_API_KEY",
    )
    parser.add_argument("--projects", type=int, default=DEFAULT_PROJECT_COUNT)
    parser.add_argument("--timeout", type=float, default=20.0)
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


def _normalize_project_count(value: int) -> int | None:
    count = int(value)
    if count < 1 or count > MAX_PROJECT_COUNT:
        return None
    return count


def _local_url(raw: str | None) -> str | None:
    value = str(raw or "").strip()
    try:
        parsed = urlsplit(value)
    except ValueError:
        return None
    host = (parsed.hostname or "").lower().strip("[]")
    if parsed.scheme not in {"http", "https"} or host not in LOCAL_HOSTS:
        return None
    return value.rstrip("/")


def _blocked(
    reason: str,
    *,
    url_configured: bool = False,
    database_configured: bool = False,
    local_database_access_performed: bool = False,
    network_access_performed: bool = False,
    mutation_performed: bool = False,
) -> dict[str, Any]:
    return {
        "schema_version": CHECK_SCHEMA,
        "mode": "local-postgres-many-project",
        "status": "blocked",
        "reason": reason,
        "url_configured": url_configured,
        "database_configured": database_configured,
        "mutation_performed": mutation_performed,
        "local_database_access_performed": local_database_access_performed,
        "network_access_performed": network_access_performed,
        "external_network_access_performed": False,
        "external_provider_mutation_performed": False,
        "licence_metadata_is_gate": False,
        "scope": "durable projects table, authenticated local /metrics scrape, and scoped cleanup",
    }


async def _migration_version(storage: AgentStorage) -> str | None:
    async with storage.engine.connect() as connection:
        return await connection.scalar(sa.text("SELECT version_num FROM alembic_version"))


async def _fixture_count(storage: AgentStorage) -> int:
    async with storage.engine.connect() as connection:
        value = await connection.scalar(
            sa.text(
                """SELECT count(*)
                   FROM projects
                   WHERE name LIKE :prefix AND created_by = :created_by"""
            ),
            {"prefix": PROJECT_PREFIX_PATTERN, "created_by": FIXTURE_CREATED_BY},
        )
    return int(value or 0)


async def _cleanup(storage: AgentStorage) -> int:
    """Delete only the reserved fixture namespace."""

    async with storage.engine.begin() as connection:
        result = await connection.execute(
            sa.text(
                """DELETE FROM projects
                   WHERE name LIKE :prefix AND created_by = :created_by"""
            ),
            {"prefix": PROJECT_PREFIX_PATTERN, "created_by": FIXTURE_CREATED_BY},
        )
    return int(result.rowcount or 0)


async def _insert_fixture(storage: AgentStorage, count: int) -> int:
    """Insert a bounded, payload-free project population in one transaction."""

    now = datetime.now(UTC)
    rows = [
        {
            "id": uuid4(),
            "name": f"{PROJECT_PREFIX}{index:05d}",
            "description": None,
            "state": PROJECT_STATES[index % len(PROJECT_STATES)],
            "failure_reason": None,
            "failed_from_state": None,
            "created_by": FIXTURE_CREATED_BY,
            "human_requester": None,
            "company_id": DEFAULT_COMPANY_ID,
            "config": {"fixture": "metric-series-many-project-v1"},
            "revision": 1,
            "created_at": now,
            "updated_at": now,
        }
        for index in range(count)
    ]
    inserted = 0
    async with storage.engine.begin() as connection:
        for start in range(0, len(rows), 1_000):
            batch = rows[start : start + 1_000]
            await connection.execute(memory_models.projects.insert(), batch)
            inserted += len(batch)
    return inserted


def _scrape_projection(
    family_counts: dict[str, int],
    labels: dict[str, set[str]],
    project_state_values: set[str],
) -> dict[str, Any]:
    declared_labels = metric_declared_label_inventory()
    label_policies = metric_label_policy_inventory()
    passed, violations = _status(
        family_counts=family_counts,
        labels=labels,
        declared_labels=declared_labels,
        label_policies=label_policies,
    )
    return {
        "status": "pass" if passed else "fail",
        "total": sum(family_counts.values()),
        "family_counts": dict(family_counts),
        "label_inventory": {name: sorted(values) for name, values in labels.items()},
        "project_state_values": sorted(project_state_values),
        "violations": list(violations),
        "project_id_label_present": any(
            "project_id" in values for values in labels.values()
        ),
    }


def _fetch_scrape(url: str, api_key: str, timeout: float) -> dict[str, Any]:
    headers = {"X-API-Key": api_key}
    with httpx.Client(timeout=timeout, headers=headers, trust_env=False) as client:
        response = client.get(f"{url}/metrics")
        response.raise_for_status()
    body = response.text
    family_counts, labels = _parse_scrape(body)
    project_state_values: set[str] = set()
    for family in text_string_to_metric_families(body):
        if str(family.name) != "mas_project_state":
            continue
        for sample in family.samples:
            state = sample.labels.get("state")
            if state is not None:
                project_state_values.add(str(state))
    return _scrape_projection(family_counts, labels, project_state_values)


async def _run(
    *,
    dsn: str | None,
    url: str | None,
    api_key: str | None,
    project_count: int,
    timeout: float,
) -> dict[str, Any]:
    normalized_dsn = _normalize_dsn(dsn)
    local_url = _local_url(url)
    normalized_count = _normalize_project_count(project_count)
    if normalized_dsn is None:
        return _blocked("metric_series_database_not_configured")
    if local_url is None:
        return _blocked("metric_series_orchestrator_url_must_be_local")
    if not str(api_key or "").strip():
        return _blocked("metric_series_operator_api_key_not_configured", url_configured=True, database_configured=True)
    if normalized_count is None:
        return _blocked(
            f"metric_series_project_count_out_of_range: 1..{MAX_PROJECT_COUNT}",
            url_configured=True,
            database_configured=True,
        )

    storage = AgentStorage(normalized_dsn)
    database_access = False
    network_access = False
    mutation_performed = False
    preexisting_count = 0
    initial_cleanup_count = 0
    inserted_count = 0
    durable_count = 0
    cleanup_count = 0
    remaining_count = 0
    migration_version: str | None = None
    baseline: dict[str, Any] | None = None
    fixture: dict[str, Any] | None = None
    restored: dict[str, Any] | None = None
    try:
        await storage.connect()
        database_access = True
        migration_version = await _migration_version(storage)
        if migration_version != EXPECTED_MIGRATION:
            return {
                **_blocked(
                    "metric_series_migration_not_at_head",
                    url_configured=True,
                    database_configured=True,
                    local_database_access_performed=True,
                ),
                "migration_version": migration_version,
                "expected_migration": EXPECTED_MIGRATION,
            }

        preexisting_count = await _fixture_count(storage)
        initial_cleanup_count = await _cleanup(storage)
        mutation_performed = bool(initial_cleanup_count)
        if await _fixture_count(storage):
            return {
                **_blocked(
                    "metric_series_initial_fixture_cleanup_incomplete",
                    url_configured=True,
                    database_configured=True,
                    local_database_access_performed=True,
                    mutation_performed=mutation_performed,
                ),
                "migration_version": migration_version,
                "preexisting_fixture_count": preexisting_count,
                "initial_cleanup_count": initial_cleanup_count,
            }

        baseline = _fetch_scrape(local_url, str(api_key), timeout)
        network_access = True
        inserted_count = await _insert_fixture(storage, normalized_count)
        mutation_performed = True
        durable_count = await _fixture_count(storage)
        fixture = _fetch_scrape(local_url, str(api_key), timeout)
        network_access = True
        cleanup_count = await _cleanup(storage)
        remaining_count = await _fixture_count(storage)
        restored = _fetch_scrape(local_url, str(api_key), timeout)
        network_access = True
    except (httpx.HTTPError, ValueError) as exc:
        return {
            **_blocked(
                "metric_series_local_scrape_unavailable",
                url_configured=True,
                database_configured=True,
                local_database_access_performed=database_access,
                network_access_performed=network_access,
                mutation_performed=mutation_performed,
            ),
            "migration_version": migration_version,
            "failure_type": type(exc).__name__,
        }
    except Exception as exc:  # pragma: no cover - exercised by live deployment failures
        return {
            **_blocked(
                "metric_series_local_postgres_evidence_failed",
                url_configured=True,
                database_configured=True,
                local_database_access_performed=database_access,
                network_access_performed=network_access,
                mutation_performed=mutation_performed,
            ),
            "migration_version": migration_version,
            "failure_type": type(exc).__name__,
        }
    finally:
        if getattr(storage, "_engine", None) is not None:
            with suppress(Exception):
                if mutation_performed:
                    await _cleanup(storage)
            with suppress(Exception):
                await storage.close()

    assert baseline is not None
    assert fixture is not None
    assert restored is not None
    fixture_missing_states = sorted(
        set(PROJECT_STATES) - set(fixture.get("project_state_values", []))
    )
    if fixture_missing_states:
        fixture["violations"].append(
            "fixture scrape is missing durable project-state labels: "
            + ", ".join(fixture_missing_states)
        )
        fixture["status"] = "fail"
    restored_matches_baseline = (
        restored["family_counts"] == baseline["family_counts"]
        and restored["label_inventory"] == baseline["label_inventory"]
    )
    if not restored_matches_baseline:
        restored["violations"].append("restored scrape differs from the baseline scrape")
        restored["status"] = "fail"

    passed = (
        migration_version == EXPECTED_MIGRATION
        and baseline["status"] == "pass"
        and fixture["status"] == "pass"
        and restored["status"] == "pass"
        and inserted_count == normalized_count
        and durable_count == normalized_count
        and cleanup_count == normalized_count
        and remaining_count == 0
        and not baseline["project_id_label_present"]
        and not fixture["project_id_label_present"]
        and not restored["project_id_label_present"]
        and restored_matches_baseline
    )
    return {
        "schema_version": CHECK_SCHEMA,
        "mode": "local-postgres-many-project",
        "status": "pass" if passed else "fail",
        "observed_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "migration_version": migration_version,
        "expected_migration": EXPECTED_MIGRATION,
        "synthetic_project_count": normalized_count,
        "preexisting_fixture_count": preexisting_count,
        "initial_cleanup_count": initial_cleanup_count,
        "inserted_project_count": inserted_count,
        "durable_project_count": durable_count,
        "cleanup_deleted_count": cleanup_count,
        "remaining_fixture_count": remaining_count,
        "budget": METRIC_SERIES_BUDGET,
        "family_budgets": dict(METRIC_FAMILY_SERIES_BUDGETS),
        "expected_project_state_labels": list(PROJECT_STATES),
        "baseline_scrape": baseline,
        "fixture_scrape": fixture,
        "restored_scrape": restored,
        "restored_matches_baseline": restored_matches_baseline,
        "project_id_label_present": any(
            scrape["project_id_label_present"]
            for scrape in (baseline, fixture, restored)
        ),
        "mutation_performed": True,
        "local_database_access_performed": True,
        "network_access_performed": True,
        "external_network_access_performed": False,
        "external_provider_mutation_performed": False,
        "licence_metadata_is_gate": False,
        "scope": "native durable project-state reconciliation on an authenticated local orchestrator scrape",
        "certification_boundary": {
            "many_project_rows_inserted_and_read_back": "checked",
            "aggregate_bounded_metric_families": "checked",
            "project_id_label_absent": "checked",
            "scoped_fixture_cleanup_and_scrape_restore": "checked",
            "load_soak_chaos_and_disaster_recovery": "not_checked",
            "live_model_backed_worker": "not_checked",
        },
    }


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    report = asyncio.run(
        _run(
            dsn=args.dsn,
            url=args.url,
            api_key=args.api_key,
            project_count=args.projects,
            timeout=args.timeout,
        )
    )
    if args.json:
        print(json.dumps(report, default=str, sort_keys=True, indent=2))
    else:
        print(f"metric-series many-project certification: {report['status']}")
    return {"pass": 0, "fail": 1, "blocked": 2}[str(report["status"])]


if __name__ == "__main__":
    raise SystemExit(main())

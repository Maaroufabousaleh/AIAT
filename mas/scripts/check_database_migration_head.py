"""Check that the source and deployed AIAT database migration heads agree.

The static mode validates the checked-in Alembic graph.  The optional live mode
performs one read-only ``alembic_version`` query and reports schema drift as a
blocked deployment prerequisite.  It never upgrades, downgrades, or mutates a
database, and it never emits a DSN or credential value.
"""

from __future__ import annotations

import argparse
import ast
import asyncio
import json
import os
from pathlib import Path
from typing import Any

import sqlalchemy as sa
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import create_async_engine

MAS_ROOT = Path(__file__).resolve().parents[1]
MIGRATION_DIR = MAS_ROOT / "migrations" / "versions"
EXPECTED_MIGRATION_HEAD = "0045_worker_tool_effects"
CHECK_SCHEMA = "aiat.database-migration-head-check.v1"


def _literal(node: ast.AST | None) -> Any:
    if node is None:
        return None
    try:
        return ast.literal_eval(node)
    except (ValueError, TypeError, SyntaxError):
        return None


def _string_values(value: Any) -> set[str]:
    if isinstance(value, str) and value:
        return {value}
    if isinstance(value, (tuple, list)):
        return {item for item in value if isinstance(item, str) and item}
    return set()


def source_migration_heads(*, migration_dir: Path = MIGRATION_DIR) -> tuple[set[str], set[str]]:
    """Return ``(revision_ids, graph_heads)`` from checked-in migration files."""

    revisions: set[str] = set()
    down_revisions: set[str] = set()
    for path in sorted(migration_dir.glob("*.py")):
        if path.name == "__init__.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        values: dict[str, Any] = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                targets = node.targets
            elif isinstance(node, ast.AnnAssign):
                targets = [node.target]
            else:
                continue
            for target in targets:
                if isinstance(target, ast.Name) and target.id in {"revision", "down_revision"}:
                    values[target.id] = _literal(node.value)
        revisions.update(_string_values(values.get("revision")))
        down_revisions.update(_string_values(values.get("down_revision")))
    return revisions, revisions - down_revisions


def _normalize_dsn(raw: str | None) -> str | None:
    value = str(raw or "").strip()
    if not value or "${" in value or "}" in value:
        return None
    if value.startswith("postgresql://"):
        return "postgresql+asyncpg://" + value.removeprefix("postgresql://")
    if value.startswith("postgres://"):
        return "postgresql+asyncpg://" + value.removeprefix("postgres://")
    if value.startswith("postgresql+asyncpg://"):
        return value
    return None


def _base_report(*, live: bool) -> dict[str, Any]:
    return {
        "schema_version": CHECK_SCHEMA,
        "mode": "live" if live else "static",
        "status": "blocked" if live else "pass",
        "expected_migration_head": EXPECTED_MIGRATION_HEAD,
        "migration_source_heads": [],
        "migration_source_revision_count": 0,
        "live_database_head": None,
        "local_database_access_performed": False,
        "mutation_performed": False,
        "external_network_access_performed": False,
        "external_provider_mutation_performed": False,
        "licence_metadata_is_gate": False,
    }


def _static_report() -> dict[str, Any]:
    report = _base_report(live=False)
    try:
        revisions, heads = source_migration_heads()
    except (OSError, SyntaxError) as exc:
        report.update(status="fail", reason=f"source migration graph could not be read: {type(exc).__name__}")
        return report
    report["migration_source_heads"] = sorted(heads)
    report["migration_source_revision_count"] = len(revisions)
    if heads != {EXPECTED_MIGRATION_HEAD}:
        report.update(
            status="fail",
            reason=(
                "source migration graph does not have the expected single head "
                f"{EXPECTED_MIGRATION_HEAD!r}"
            ),
        )
    else:
        report["reason"] = "source migration graph has the expected single head"
    return report


async def _read_live_head(dsn: str) -> tuple[str | None, str | None]:
    engine = create_async_engine(dsn, pool_pre_ping=True)
    try:
        async with engine.connect() as connection:
            value = await connection.scalar(sa.text("SELECT version_num FROM alembic_version"))
        return (str(value) if value is not None else None), None
    except (OSError, SQLAlchemyError) as exc:
        return None, type(exc).__name__
    finally:
        await engine.dispose()


def _live_report(dsn: str | None) -> dict[str, Any]:
    report = _static_report()
    report["mode"] = "live"
    if report["status"] == "fail":
        report["status"] = "blocked"
        report["reason"] = "source migration graph is not valid for live comparison"
        return report
    normalized = _normalize_dsn(dsn)
    if normalized is None:
        report.update(
            status="blocked",
            reason="a readable migration-check DSN was not configured",
        )
        return report
    live_head, error = asyncio.run(_read_live_head(normalized))
    report["local_database_access_performed"] = True
    report["live_database_head"] = live_head
    if error is not None:
        report.update(status="blocked", reason=f"migration head query failed: {error}")
    elif live_head != EXPECTED_MIGRATION_HEAD:
        report.update(
            status="blocked",
            reason=(
                "deployed database migration head differs from source: "
                f"live={live_head!r}, expected={EXPECTED_MIGRATION_HEAD!r}"
            ),
        )
    else:
        report["status"] = "pass"
        report["reason"] = "source and deployed migration heads agree"
    return report


def build_report(*, live: bool = False, dsn: str | None = None) -> dict[str, Any]:
    return _live_report(dsn or os.getenv("AIAT_MIGRATION_HEAD_DSN") or os.getenv("PGBOUNCER_DSN") or os.getenv("POSTGRES_DSN")) if live else _static_report()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="emit JSON")
    parser.add_argument("--live", action="store_true", help="perform a read-only database query")
    parser.add_argument("--dsn", help="read-only Postgres DSN; never included in output")
    args = parser.parse_args(argv)
    report = build_report(live=args.live, dsn=args.dsn)
    if args.json:
        print(json.dumps(report, sort_keys=True, indent=2))
    else:
        print(f"database migration head: {report['status']}")
    return {"pass": 0, "blocked": 2, "fail": 1}[report["status"]]


if __name__ == "__main__":
    raise SystemExit(main())

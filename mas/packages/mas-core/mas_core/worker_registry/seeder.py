"""Manifest-to-DB seeder.

Reads ``workers/*.yaml`` files, parses them into ``WorkerManifest`` objects,
and upserts capabilities + workers into the database via ``AgentStorage``.

Usage
-----
Programmatic::

    from mas_core.worker_registry.seeder import seed_workers_from_directory
    await seed_workers_from_directory(storage, workers_dir=Path("workers/"))

CLI::

    python -m mas_core.worker_registry.seeder --workers-dir workers/ --dry-run
"""

from __future__ import annotations

import argparse
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any
from uuid import UUID

import yaml

from mas_core.protocols.worker_manifest import WorkerManifest

if TYPE_CHECKING:
    from mas_core.memory.storage import AgentStorage

logger = logging.getLogger(__name__)

SEEDER_VERSION = "1.0.0"


class SeedResult:
    """Outcome of seeding one worker manifest."""

    def __init__(self, worker_id: str, action: str, details: str = "") -> None:
        self.worker_id = worker_id
        self.action = action  # "created", "updated", "skipped", "error"
        self.details = details

    def __repr__(self) -> str:
        return f"SeedResult({self.worker_id!r}, {self.action!r}, {self.details!r})"


async def seed_workers_from_directory(
    storage: AgentStorage,
    *,
    workers_dir: Path,
    dry_run: bool = False,
    default_evaluation_status: str = "approved",
) -> list[SeedResult]:
    """Seed all worker manifests from a directory into the database.

    Parameters
    ----------
    storage:
        Connected ``AgentStorage`` instance.
    workers_dir:
        Directory containing ``*.yaml`` worker manifest files.
    dry_run:
        If True, parse and validate manifests without writing to DB.
    default_evaluation_status:
        Status to assign when seeding from local manifests.

    Returns
    -------
    list[SeedResult]
        One result per manifest file found.
    """
    results: list[SeedResult] = []
    manifest_paths = sorted(workers_dir.glob("*.yaml"))

    if not manifest_paths:
        logger.warning("No worker manifests found in %s", workers_dir)
        return results

    logger.info("Found %d worker manifest(s) in %s", len(manifest_paths), workers_dir)

    for manifest_path in manifest_paths:
        try:
            result = await _seed_single_manifest(
                storage,
                manifest_path,
                dry_run=dry_run,
                default_evaluation_status=default_evaluation_status,
            )
            results.append(result)
        except Exception as exc:
            logger.error("Failed to seed %s: %s", manifest_path.name, exc)
            results.append(
                SeedResult(
                    worker_id=manifest_path.stem,
                    action="error",
                    details=str(exc),
                )
            )

    _log_summary(results)
    return results


async def _seed_single_manifest(
    storage: AgentStorage,
    manifest_path: Path,
    *,
    dry_run: bool,
    default_evaluation_status: str,
) -> SeedResult:
    """Parse and upsert a single worker manifest."""
    raw = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    manifest = WorkerManifest.model_validate(raw)

    worker_id = manifest.metadata.id

    if dry_run:
        logger.info("[DRY-RUN] Would seed worker %s from %s", worker_id, manifest_path.name)
        return SeedResult(worker_id, "skipped", "dry-run")

    existing = await storage.get_worker_by_name(worker_id)

    cap_ids = await _upsert_capabilities(storage, manifest)

    team_tag = _extract_team_tag(manifest)

    worker_data = await storage.register_worker(
        name=worker_id,
        adapter_type=manifest.runtime.transport,
        adapter_config=manifest.runtime.adapter_config,
        sandbox_profile=manifest.sandbox.profile,
        capability_ids=cap_ids,
        team_id=team_tag,
        status="ACTIVE",
        version=manifest.metadata.version,
        source_repo=manifest.metadata.source_repo
        if manifest.metadata.source_repo != "local"
        else None,
        source_revision=manifest.metadata.source_revision,
        version_pin=manifest.metadata.version_pin,
        update_policy=manifest.metadata.update_policy,
        evaluation_status=default_evaluation_status,
        adapter_entrypoint=manifest.integration.adapter_entrypoint,
        adapter_module=manifest.integration.adapter_module,
        wrapper_config=manifest.integration.wrapper_config,
        isolation_mode=manifest.integration.isolation_mode,
    )

    action = "updated" if existing else "created"
    logger.info("Worker %s %s (id=%s)", worker_id, action, worker_data["id"])
    return SeedResult(worker_id, action, f"id={worker_data['id']}")


async def _upsert_capabilities(
    storage: AgentStorage,
    manifest: WorkerManifest,
) -> list[UUID]:
    """Create or retrieve capabilities from the manifest and return their IDs."""
    cap_ids: list[UUID] = []

    for cap_def in manifest.capabilities:
        existing = await storage.get_capability_by_name(cap_def.name)
        if existing:
            cap_ids.append(existing["id"])
        else:
            created = await storage.create_capability(
                name=cap_def.name,
                version=cap_def.version,
                description=cap_def.description,
                input_schema=cap_def.input_schema,
                output_schema=cap_def.output_schema,
                risk_level=cap_def.risk_level,
                cost_model=cap_def.cost_model,
                required_tools=cap_def.required_tools,
                required_role=cap_def.required_role.value if cap_def.required_role else None,
            )
            cap_ids.append(created["id"])

    return cap_ids


def _extract_team_tag(manifest: WorkerManifest) -> str | None:
    """Derive a team_id from manifest tags."""
    team_prefixes = (
        "exec_",
        "office_",
        "dept_",
    )
    for tag in manifest.metadata.tags:
        for prefix in team_prefixes:
            if tag.startswith(prefix):
                return tag
    return None


def _log_summary(results: list[SeedResult]) -> None:
    """Print a summary of seeding results."""
    created = sum(1 for r in results if r.action == "created")
    updated = sum(1 for r in results if r.action == "updated")
    skipped = sum(1 for r in results if r.action == "skipped")
    errors = sum(1 for r in results if r.action == "error")

    logger.info(
        "Seeding complete: %d created, %d updated, %d skipped, %d errors",
        created,
        updated,
        skipped,
        errors,
    )

    if errors:
        for r in results:
            if r.action == "error":
                logger.error("  ERROR %s: %s", r.worker_id, r.details)


def main() -> None:
    """CLI entrypoint."""
    parser = argparse.ArgumentParser(description="Seed worker manifests into the database")
    parser.add_argument(
        "--workers-dir",
        type=Path,
        default=Path("workers"),
        help="Directory containing worker YAML manifests (default: workers/)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Parse and validate manifests without writing to DB",
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(message)s")

    if args.dry_run:
        import asyncio

        async def _run() -> None:
            results = await seed_workers_from_directory(
                storage=None,  # type: ignore[arg-type]
                workers_dir=args.workers_dir,
                dry_run=True,
            )
            for r in results:
                print(f"  [{r.action.upper():8s}] {r.worker_id}: {r.details}")

        asyncio.run(_run())
    else:
        print("Error: --dry-run is the only mode available from CLI without a DB connection.")
        print("Use the programmatic API to seed with a real AgentStorage instance.")


if __name__ == "__main__":
    main()

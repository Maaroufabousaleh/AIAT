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
from hashlib import sha256
import inspect
import json
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
    adapter_config, status, evaluation_status = _seed_governance_state(manifest)
    # Startup seeding reconciles a declaration; it is not a lifecycle
    # transition.  In particular, an operator may already have promoted the
    # immutable shell/adapter/bundle for this worker.  Never turn that
    # governed ACTIVE row back into an inert compatibility import merely
    # because its YAML manifest is read again on process restart.
    persisted_status = existing.get("status") if existing is not None else None
    effective_status = str(persisted_status or status)
    persisted_evaluation_status = (
        existing.get("evaluation_status") if existing is not None else None
    )
    effective_evaluation_status = (
        persisted_evaluation_status
        if persisted_evaluation_status is not None
        else evaluation_status
    )

    worker_data = await storage.register_worker(
        name=worker_id,
        adapter_type=manifest.runtime.transport,
        adapter_config=adapter_config,
        sandbox_profile=manifest.sandbox.profile,
        capability_ids=cap_ids,
        team_id=team_tag,
        # A YAML file is a declaration, not certification evidence.  In
        # particular, a fresh database has no immutable shell/adapter/bundle
        # rows yet.  Never label that worker ACTIVE merely because it shipped
        # with the image; activation is a governed control-plane transition.
        status=effective_status,
        version=manifest.metadata.version,
        source_repo=manifest.metadata.source_repo
        if manifest.metadata.source_repo != "local"
        else None,
        source_revision=manifest.metadata.source_revision,
        version_pin=manifest.metadata.version_pin,
        update_policy=manifest.metadata.update_policy,
        evaluation_status=effective_evaluation_status,
        adapter_entrypoint=manifest.integration.adapter_entrypoint,
        adapter_module=manifest.integration.adapter_module,
        wrapper_config=manifest.integration.wrapper_config,
        isolation_mode=manifest.integration.isolation_mode,
        model_mode=manifest.model_mode,
        model_profile_id=manifest.model_profile_id,
    )

    # An externally backed manifest starts with a dedicated Steward record
    # and an idempotent monitoring job.  It remains inactive until the
    # steward has produced a pinned, certified candidate and an operator has
    # promoted the immutable versions.  Local AIAT shells do not need a
    # steward solely for being seeded from this repository.
    if manifest.metadata.source_repo and manifest.metadata.source_repo != "local":
        await _bootstrap_external_steward(storage, worker_data, manifest)

    action = "updated" if existing else "created"
    detail = f"id={worker_data['id']} status={effective_status.lower()}"
    if effective_status != "ACTIVE":
        detail += f" blockers={','.join(adapter_config['activation_blockers'])}"
    logger.info("Worker %s %s (%s)", worker_id, action, detail)
    return SeedResult(worker_id, action, detail)


def _seed_governance_state(manifest: WorkerManifest) -> tuple[dict[str, Any], str, str]:
    """Return a safe initial registry state for a declared worker shell.

    The registry used to turn every YAML declaration into an ACTIVE worker.
    That bypassed the universal-contract activation requirements on fresh
    deployments and made legacy Python wrappers look dispatchable.  YAML is
    still imported so operators can migrate it, but no declaration is trusted
    as certification, provenance, or a governed model decision.
    """

    config = dict(manifest.runtime.adapter_config)
    blockers = [
        "missing immutable WorkerShell version",
        "missing certified runtime Adapter",
        "missing approved Skill Bundle",
        "missing capability snapshot",
    ]
    if not manifest.metadata.version_pin:
        blockers.append("missing immutable source/version pin")
    if manifest.model_mode != "none" and not manifest.model_profile_id:
        blockers.append("missing approved Model Profile")
    if manifest.is_legacy_external_wrapper:
        blockers.append("legacy external wrapper requires certified runtime-specific adapter")

    config.update(
        {
            "governance_required": True,
            "governance_status": "compatibility",
            "migration_status": manifest.metadata.migration_status,
            "legacy_external_wrapper": manifest.is_legacy_external_wrapper,
            "activation_blockers": blockers,
        }
    )
    # Only a future seeder path that creates and verifies the immutable
    # records can choose ACTIVE.  Keeping all declarative imports inactive is
    # deliberate: it prevents a seed-time claim from becoming production
    # authority.
    return config, "INACTIVE", "compatibility_pending_governance"


async def _bootstrap_external_steward(
    storage: AgentStorage,
    worker: dict[str, Any],
    manifest: WorkerManifest,
) -> None:
    """Create the non-authoritative external-worker intake records when supported.

    This helper is intentionally capability-detected so old lightweight
    storage test doubles and pre-governance databases can still read/import
    manifests.  It does not create a provenance row from incomplete metadata:
    the steward intake API owns validation of the required immutable pin.
    """

    required = (
        "get_steward_by_worker",
        "get_external_provenance_by_worker",
        "create_external_provenance",
        "create_steward",
        "list_update_monitoring_jobs",
        "create_update_monitoring_job",
    )
    if not all(inspect.iscoroutinefunction(getattr(storage, name, None)) for name in required):
        return

    from mas_core.worker_registry.steward import ExternalProvenance

    raw_provenance = dict(manifest.source_provenance)
    raw_provenance.setdefault("canonical_source_repository", manifest.metadata.source_repo)
    raw_provenance.setdefault("source_provider", "github" if "github.com" in str(manifest.metadata.source_repo) else "external")
    raw_provenance.setdefault("exact_release", manifest.metadata.version_pin)
    raw_provenance.setdefault("transport_type", manifest.runtime.transport)
    raw_provenance.setdefault("adapter_version", manifest.integration.certified_adapter_version or manifest.metadata.version)
    raw_provenance.setdefault("protocol_api_version", manifest.integration.contract_version)
    provenance = ExternalProvenance.model_validate(raw_provenance)
    provenance_json = provenance.model_dump(mode="json")
    existing_provenance = await storage.get_external_provenance_by_worker(worker["id"])
    provenance_row = existing_provenance or await storage.create_external_provenance(
        worker_id=worker["id"],
        provenance=provenance_json,
        provenance_hash=sha256(
            json.dumps(provenance_json, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest(),
    )

    existing = await storage.get_steward_by_worker(worker["id"])
    if existing is None:
        existing = await storage.create_steward(
            worker_id=worker["id"],
            provenance_id=provenance_row["id"],
            status="PROVISIONING",
            monitoring_cadence="daily",
            metadata={
                "seeded_from_manifest": manifest.metadata.id,
                "source_repository": manifest.metadata.source_repo,
                "version_pin": manifest.metadata.version_pin,
            },
        )
    jobs = await storage.list_update_monitoring_jobs(worker_id=worker["id"], limit=100)
    if not any(
        str(job.get("steward_id")) == str(existing["id"])
        and job.get("status") == "active"
        for job in jobs
    ):
        await storage.create_update_monitoring_job(
            worker_id=worker["id"],
            steward_id=existing["id"],
            cadence="daily",
        )
    if inspect.iscoroutinefunction(getattr(storage, "update_worker_config", None)):
        config = {
            **dict(worker.get("adapter_config") or {}),
            "steward_id": str(existing["id"]),
            "governance_required": True,
        }
        await storage.update_worker_config(worker["id"], adapter_config=config)


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

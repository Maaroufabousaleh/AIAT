"""Scheduled, non-authoritative monitoring for externally backed workers.

Monitoring is deliberately narrow: it compares an approved source against its
remote pin and, when it observes a new commit, creates a *DRAFT/DISCOVERED*
candidate.  It never edits a worker's active adapter, shell, bundle, model, or
provenance pointers.  Certification, approval, canary, and promotion remain
separate control-plane transitions.
"""

from __future__ import annotations

import hashlib
import inspect
import json
import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse
from uuid import UUID, uuid4

from .ingestion import check_for_updates

if TYPE_CHECKING:
    from mas_core.memory.storage import AgentStorage

logger = logging.getLogger(__name__)

UpdateChecker = Callable[..., Awaitable[dict[str, str | bool | None]]]

_CADENCE_INTERVALS = {
    "hourly": timedelta(hours=1),
    "daily": timedelta(days=1),
    "weekly": timedelta(days=7),
}


def _is_due(job: dict[str, Any], *, now: datetime) -> bool:
    """Return whether one durable monitoring job is due for a remote check."""

    if str(job.get("status", "active")).lower() != "active":
        return False
    last_checked = job.get("last_checked_at")
    if last_checked is None:
        return True
    if isinstance(last_checked, str):
        last_checked = datetime.fromisoformat(last_checked.replace("Z", "+00:00"))
    if last_checked.tzinfo is None:
        last_checked = last_checked.replace(tzinfo=UTC)
    cadence = str(job.get("cadence") or "daily").lower()
    return now >= last_checked + _CADENCE_INTERVALS.get(cadence, _CADENCE_INTERVALS["daily"])


def _safe_remote_source(source_repo: Any) -> str:
    """Reject sources that cannot be passed safely to the git monitor.

    Hiring intake may retain arbitrary text for review, but the automated
    monitor only handles canonical HTTPS sources.  This avoids treating an
    unreviewed SSH command, local path, or option-looking string as a git
    argument from a background process.
    """

    source = str(source_repo or "").strip()
    parsed = urlparse(source)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or source.startswith("-")
        or "\x00" in source
    ):
        raise ValueError("automatic monitoring requires a canonical HTTPS source repository")
    return source


def _candidate_version(latest_commit: str) -> str:
    return f"upstream-{latest_commit[:12].lower()}"


def _json_safe(value: Any) -> Any:
    """Return immutable candidate evidence in the storage JSON shape.

    Storage rows contain native ``datetime`` and ``UUID`` objects while the
    skill-bundle and candidate evidence columns deliberately store JSON.  An
    update observation must be auditable even when the upstream provenance
    carries those native values.
    """

    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return value


async def _create_discovered_candidate(
    storage: AgentStorage,
    *,
    job: dict[str, Any],
    worker: dict[str, Any],
    provenance: dict[str, Any],
    latest_commit: str,
) -> UUID | None:
    """Persist one immutable candidate for a newly observed upstream commit."""

    worker_id = UUID(str(worker["id"]))
    steward_id = UUID(str(job["steward_id"]))
    candidates = await storage.list_skill_bundle_candidates(worker_id)
    for candidate in candidates:
        observed = (candidate.get("diff_json") or {}).get("monitoring", {}).get("latest_commit")
        if observed == latest_commit:
            return UUID(str(candidate["id"]))

    candidate_provenance = _json_safe(dict(provenance))
    # A discovered remote commit is candidate provenance only.  It does not
    # overwrite the active external_runtime_provenance row.
    candidate_provenance["commit_sha"] = latest_commit
    candidate_provenance["exact_release"] = None
    candidate_provenance["last_verified_documentation_at"] = None
    version = _candidate_version(latest_commit)
    transport = str(worker.get("adapter_type") or provenance.get("transport_type") or "process")
    config = dict(worker.get("adapter_config") or {})
    capabilities = dict(config.get("capabilities") or {})
    adapter_hash = hashlib.sha256(
        json.dumps(
            {
                "worker_id": str(worker_id),
                "version": version,
                "transport": transport,
                "latest_commit": latest_commit,
                "entrypoint": config.get("entrypoint"),
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    adapter = None
    list_adapters = getattr(storage, "list_runtime_adapters", None)
    if inspect.iscoroutinefunction(list_adapters):
        for existing in await list_adapters(worker_id):
            if existing.get("version") != version:
                continue
            if (
                existing.get("content_hash") != adapter_hash
                or existing.get("status") != "candidate"
            ):
                raise ValueError(
                    "candidate adapter version already exists with different immutable content"
                )
            adapter = existing
            break
    if adapter is None:
        adapter = await storage.create_runtime_adapter(
            worker_id=worker_id,
            version=version,
            adapter_type=str(worker.get("isolation_mode") or "external"),
            transport_type=transport,
            content_hash=adapter_hash,
            runtime_api_version=provenance.get("protocol_api_version"),
            implementation_ref=config.get("implementation_ref") or config.get("entrypoint"),
            capabilities=capabilities,
            conformance_status="pending",
            conformance={"source": "update_monitor", "latest_commit": latest_commit},
            status="candidate",
        )
    bundle_payload = {
        "source": "update_monitor",
        "candidate_provenance": candidate_provenance,
        "adapter_id": str(adapter["id"]),
        "activation_prohibited": True,
        "required_next_stages": [
            "SOURCE_REVIEW",
            "SECURITY_REVIEW",
            "INTERFACE_RESEARCH",
            "CERTIFYING",
            "APPROVED",
            "CANARY",
        ],
        "optional_metadata_stages": ["LICENSE_REVIEW"],
    }
    bundle_hash = hashlib.sha256(
        json.dumps(bundle_payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    bundle = None
    list_bundles = getattr(storage, "list_skill_bundles", None)
    if inspect.iscoroutinefunction(list_bundles):
        for existing in await list_bundles(worker_id, steward_id=steward_id):
            if existing.get("semantic_version") != version:
                continue
            if (
                existing.get("content_hash") != bundle_hash
                or existing.get("status") != "DRAFT"
            ):
                raise ValueError(
                    "candidate skill bundle version already exists with different immutable content"
                )
            bundle = existing
            break
    if bundle is None:
        bundle = await storage.create_skill_bundle(
            worker_id=worker_id,
            steward_id=steward_id,
            semantic_version=version,
            format_version="aiat.skill-bundle.v1",
            upstream_compatibility_range=latest_commit,
            provenance=candidate_provenance,
            bundle=bundle_payload,
            content_hash=bundle_hash,
            status="DRAFT",
        )
    candidate_id = uuid4()
    await storage.create_skill_bundle_candidate(
        candidate_id=candidate_id,
        skill_bundle_id=bundle["id"],
        worker_id=worker_id,
        adapter_id=adapter["id"],
        intake_status="DISCOVERED",
        diff={
            "monitoring": {
                "previous_commit": provenance.get("commit_sha") or worker.get("upstream_commit_sha"),
                "latest_commit": latest_commit,
                "observed_by_job": str(job["id"]),
            }
        },
        evidence={
            "source": "scheduled_update_monitor",
            "activation_prohibited": True,
            "candidate_provenance": candidate_provenance,
        },
    )
    return candidate_id


async def run_update_monitor_job(
    storage: AgentStorage,
    job: dict[str, Any],
    *,
    checker: UpdateChecker = check_for_updates,
    now: datetime | None = None,
    force: bool = False,
) -> dict[str, Any]:
    """Run one persisted monitoring job and return its auditable outcome."""

    checked_at = now or datetime.now(tz=UTC)
    if not force and not _is_due(job, now=checked_at):
        return {"job_id": str(job["id"]), "status": "not_due", "candidate_id": None}

    worker = await storage.get_worker(UUID(str(job["worker_id"])))
    if worker is None:
        await storage.record_update_monitoring_result(
            UUID(str(job["id"])), last_error="worker no longer exists", status="inactive"
        )
        return {"job_id": str(job["id"]), "status": "worker_missing", "candidate_id": None}
    try:
        provenance = await storage.get_external_provenance_by_worker(worker["id"])
        if provenance is None:
            raise ValueError("immutable external provenance has not been recorded")
        # Mutable registry fields are operational metadata, not provenance.
        # Monitoring must use the source and revision that were certified and
        # from which the candidate evidence is subsequently derived.
        source_repo = _safe_remote_source(provenance.get("canonical_source_repository"))
        result = await checker(
            source_repo=source_repo,
            current_revision=provenance.get("exact_release") or provenance.get("commit_sha"),
            current_commit=provenance.get("commit_sha"),
        )
        latest_commit = str(result.get("latest_commit") or "")
        if not latest_commit:
            raise ValueError("upstream monitor did not return a commit SHA")
        candidate_id = None
        if bool(result.get("has_updates")):
            candidate_id = await _create_discovered_candidate(
                storage,
                job=job,
                worker=worker,
                provenance=provenance,
                latest_commit=latest_commit,
            )
        await storage.record_update_monitoring_result(
            UUID(str(job["id"])), last_candidate_id=candidate_id, status="active"
        )
        if inspect.iscoroutinefunction(getattr(storage, "update_steward", None)):
            await storage.update_steward(UUID(str(job["steward_id"])), last_monitor_at=checked_at)
        return {
            "job_id": str(job["id"]),
            "status": "candidate_discovered" if candidate_id else "no_change",
            "candidate_id": str(candidate_id) if candidate_id else None,
            "latest_commit": latest_commit,
        }
    except Exception as exc:
        logger.warning("worker_update_monitor_failed", extra={"job_id": str(job["id"]), "error": str(exc)})
        await storage.record_update_monitoring_result(
            UUID(str(job["id"])), last_error=str(exc)[:2000], status="active"
        )
        return {"job_id": str(job["id"]), "status": "error", "candidate_id": None, "error": str(exc)}


async def run_due_update_monitors(
    storage: AgentStorage,
    *,
    checker: UpdateChecker = check_for_updates,
    now: datetime | None = None,
    force_worker_id: UUID | None = None,
) -> list[dict[str, Any]]:
    """Run due external-worker jobs without changing active worker pointers."""

    checked_at = now or datetime.now(tz=UTC)
    jobs = await storage.list_update_monitoring_jobs(
        worker_id=force_worker_id,
        limit=1000,
    )
    return [
        await run_update_monitor_job(
            storage,
            job,
            checker=checker,
            now=checked_at,
            force=force_worker_id is not None,
        )
        for job in jobs
    ]

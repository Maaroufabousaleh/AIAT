"""Reconcile worker manifests with the runtime, company, and provenance contracts.

The default mode is a static declaration check. ``--live`` adds a read-only
reconciliation against ``GET /capabilities/workers`` so checked-in defaults
cannot silently diverge from persisted adapter/model/sandbox bindings. It
does not certify a worker, run a runtime, or turn licence metadata into a
gate. Runtime/package availability, security scans, image digests, and live
network behaviour remain separate release evidence.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import sys
from pathlib import Path
from typing import Any, get_args
from urllib.parse import urlparse

import httpx
import yaml
from pydantic import ValidationError

from mas_core.protocols.worker_manifest import RUNTIME_TIER_LITERAL, WorkerManifest
from mas_core.worker_registry.runtime_catalog import RUNTIME_CATALOG

MAS_ROOT = Path(__file__).resolve().parents[1]
PROGRAMME_ROOT = MAS_ROOT.parent
DEFAULT_WORKERS_DIR = MAS_ROOT / "workers"
DEFAULT_COMPANY_MANIFEST = MAS_ROOT / "companies" / "default-software-company.yaml"
DEFAULT_PROVENANCE = MAS_ROOT / "docs" / "provenance" / "third_party_components.yaml"
DEFAULT_IMAGE_PROVENANCE = MAS_ROOT / "docs" / "provenance" / "production_images.yaml"
DEFAULT_COMPOSE = MAS_ROOT / "infra" / "compose" / "docker-compose.yml"
DEFAULT_SECURITY_EVIDENCE = MAS_ROOT / "docs" / "provenance" / "security_scan_evidence.yaml"
DEFAULT_NOTICES = PROGRAMME_ROOT / "THIRD_PARTY_NOTICES.md"
CHECK_SCHEMA = "aiat.worker-reconciliation.v1"

SUPPORTED_TRANSPORTS = frozenset(
    {"native", "process", "http", "oci", "mcp", "opencode", "human"}
)
CONCRETE_VERSION_RE = re.compile(r"^(?:v)?\d+(?:\.\d+){0,3}(?:[-+][0-9A-Za-z.-]+)?$")


def _load_yaml(path: Path) -> dict[str, Any]:
    value = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path}: expected a YAML mapping")
    return value


def _normalise_source(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw or raw.lower() == "local":
        return raw.lower()
    parsed = urlparse(raw if "://" in raw else f"https://{raw}")
    path = parsed.path.rstrip("/")
    if path.endswith(".git"):
        path = path[:-4]
    return f"{parsed.netloc.lower()}{path}".lower()


def _normalise_version(value: Any) -> str:
    return str(value or "").strip().removeprefix("v").lower()


def _is_concrete_version(value: Any) -> bool:
    return bool(CONCRETE_VERSION_RE.fullmatch(str(value or "").strip()))


def _environment_mapping(value: Any) -> dict[str, str]:
    if isinstance(value, dict):
        return {str(key): str(raw) for key, raw in value.items()}
    if isinstance(value, list):
        result: dict[str, str] = {}
        for item in value:
            if isinstance(item, str) and "=" in item:
                key, raw = item.split("=", 1)
                result[key] = raw
        return result
    return {}


def _runtime_availability() -> dict[str, dict[str, Any]]:
    availability: dict[str, dict[str, Any]] = {}
    for runtime_id, definition in RUNTIME_CATALOG.items():
        missing = [
            package
            for package in definition.required_imports
            if importlib.util.find_spec(package) is None
        ]
        availability[runtime_id] = {
            "status": "available" if not missing else "unavailable",
            "missing_imports": missing,
            "optional": definition.optional,
        }
    return availability


def _check_policy(
    provenance: dict[str, Any], notices_text: str, errors: list[str]
) -> None:
    policy = provenance.get("policy") or {}
    if policy.get("programme_scope") != "personal-internal-only":
        errors.append("provenance policy programme_scope must be personal-internal-only")
    if policy.get("license_handling") != "metadata-only":
        errors.append("provenance policy license_handling must be metadata-only")
    if policy.get("enforce_license_gate") is not False:
        errors.append("provenance policy enforce_license_gate must be false")
    for key in (
        "license_allowlist",
        "licence_allowlist",
        "prohibited_components",
        "prohibited_licenses",
        "prohibited_licences",
    ):
        if policy.get(key):
            errors.append(f"provenance policy must not define an active {key}")
    lower_notices = notices_text.lower()
    if "metadata-only policy" not in lower_notices:
        errors.append("THIRD_PARTY_NOTICES.md must declare the metadata-only policy")
    if "no licence allowlist" not in lower_notices:
        errors.append("THIRD_PARTY_NOTICES.md must state that no licence allowlist exists")


def _check_runtime_catalog(errors: list[str]) -> None:
    protocol_tiers = set(get_args(RUNTIME_TIER_LITERAL))
    catalogue_tiers = set(RUNTIME_CATALOG)
    missing = protocol_tiers - catalogue_tiers
    extra = catalogue_tiers - protocol_tiers
    if missing:
        errors.append(f"runtime catalogue is missing protocol tiers: {sorted(missing)}")
    if extra:
        errors.append(f"runtime catalogue contains unknown protocol tiers: {sorted(extra)}")


def _check_company_manifest(company: dict[str, Any], worker_ids: set[str], errors: list[str]) -> None:
    declared_ceo = str(company.get("ceo_worker_id") or "")
    if declared_ceo and declared_ceo not in worker_ids:
        errors.append(f"company ceo_worker_id {declared_ceo!r} has no worker manifest")
    assignments: set[str] = set()
    for department in company.get("departments") or []:
        if not isinstance(department, dict):
            errors.append("company departments must be mappings")
            continue
        for field in ("chief_worker_id",):
            worker_id = str(department.get(field) or "")
            if worker_id and worker_id not in worker_ids:
                errors.append(f"company {field} {worker_id!r} has no worker manifest")
        for worker_id in department.get("worker_ids") or []:
            worker_id = str(worker_id)
            if worker_id not in worker_ids:
                errors.append(f"company worker_ids references missing manifest {worker_id!r}")
    for assignment in company.get("worker_assignments") or []:
        if not isinstance(assignment, dict):
            errors.append("company worker_assignments must be mappings")
            continue
        worker_id = str(assignment.get("worker_id") or "")
        if worker_id not in worker_ids:
            errors.append(f"company assignment references missing manifest {worker_id!r}")
        if worker_id in assignments:
            errors.append(f"company worker {worker_id!r} has duplicate assignments")
        assignments.add(worker_id)


def _security_evidence_index(path: Path, errors: list[str]) -> dict[str, dict[str, Any]]:
    """Load the optional scan evidence index without treating findings as passed."""

    try:
        raw = _load_yaml(path)
    except (OSError, ValueError, yaml.YAMLError) as exc:
        errors.append(f"could not load security scan evidence: {exc}")
        return {}
    policy = raw.get("policy") or {}
    if policy.get("security_scan_is_technical_gate") is not True:
        errors.append("security scan evidence policy must mark security_scan_is_technical_gate true")
    if policy.get("licence_metadata_is_gate") is not False:
        errors.append("security scan evidence policy must keep licence_metadata_is_gate false")
    result: dict[str, dict[str, Any]] = {}
    for row in raw.get("scans") or []:
        if not isinstance(row, dict):
            errors.append("security scan evidence rows must be mappings")
            continue
        scan_id = str(row.get("id") or "").strip()
        if not scan_id or scan_id in result:
            errors.append("security scan evidence IDs must be unique and non-blank")
            continue
        status = str(row.get("status") or "").strip().lower()
        if status not in {"passed", "findings_review_required", "blocked"}:
            errors.append(f"{scan_id}: unsupported security scan status {status!r}")
        if not row.get("scanner") or not row.get("scanner_version"):
            errors.append(f"{scan_id}: scanner and scanner_version are required")
        if not row.get("source_repo") or not row.get("source_revision"):
            errors.append(f"{scan_id}: source_repo and source_revision are required")
        if not isinstance(row.get("target_workers"), list) or not row.get("target_workers"):
            errors.append(f"{scan_id}: target_workers must be a non-empty list")
        for field in ("scanned_file_count", "finding_count", "engine_warning_count"):
            try:
                if int(row.get(field)) < 0:
                    raise ValueError
            except (TypeError, ValueError):
                errors.append(f"{scan_id}: {field} must be a non-negative integer")
        result[scan_id] = row
    if not result:
        errors.append("security scan evidence must contain at least one scan")
    return result


def _resolve_security_evidence_ref(reference: str) -> tuple[Path, str] | None:
    raw_path, separator, scan_id = reference.partition("#")
    if not separator or not raw_path.strip() or not scan_id.strip():
        return None
    path = (PROGRAMME_ROOT / raw_path.strip()).resolve()
    if PROGRAMME_ROOT not in path.parents:
        return None
    return path, scan_id.strip()


def _check_compose_links(
    manifests: list[tuple[Path, WorkerManifest]],
    compose: dict[str, Any],
    image_inventory: dict[str, Any],
    errors: list[str],
) -> None:
    services = compose.get("services") or {}
    if not isinstance(services, dict):
        errors.append("production Compose services must be a mapping")
        return
    image_rows = {
        str(row.get("id")): row
        for row in image_inventory.get("images") or []
        if isinstance(row, dict) and row.get("id")
    }
    for path, manifest in manifests:
        transport = manifest.runtime.transport
        config = manifest.runtime.adapter_config
        if transport == "process" and not str(config.get("entrypoint") or "").strip():
            errors.append(f"{path.name}: process transport requires runtime.adapter_config.entrypoint")
        if transport == "opencode":
            base_url = str(config.get("base_url") or "").strip()
            host = urlparse(base_url).hostname
            if not host:
                errors.append(f"{path.name}: opencode transport requires an adapter base_url")
            elif host not in services:
                errors.append(f"{path.name}: opencode base_url host {host!r} is not a Compose service")
            if "opencode-runtime" not in image_rows:
                errors.append("production image inventory is missing the opencode-runtime row")
            service = services.get(host) if host else None
            build_args = ((service or {}).get("build") or {}).get("args") if isinstance(service, dict) else {}
            compose_version = str((build_args or {}).get("OPENCODE_VERSION") or "")
            manifest_version = str(manifest.metadata.version_pin or "")
            if compose_version and manifest_version and _normalise_version(compose_version) != _normalise_version(manifest_version):
                errors.append(
                    f"{path.name}: manifest version_pin {manifest_version!r} disagrees with "
                    f"Compose OPENCODE_VERSION {compose_version!r}"
                )
        for field, expected in (("adapter_entrypoint", manifest.integration.adapter_entrypoint),):
            if not str(expected or "").strip():
                errors.append(f"{path.name}: integration.{field} is required")


def _manifest_team_id(manifest: WorkerManifest) -> str | None:
    """Return the seeded team identity without trusting arbitrary tag text."""

    for tag in manifest.metadata.tags:
        if any(tag.startswith(prefix) for prefix in ("exec_", "office_", "dept_")):
            return tag
    return None


def _binding_mismatches(manifest: WorkerManifest, row: dict[str, Any]) -> list[str]:
    """Compare the API's persisted worker binding with one checked-in manifest.

    The live route intentionally checks declaration/binding fields only.  It
    does not infer security or licence approval from a worker listing; those
    remain separate technical evidence records and the metadata-only licence
    policy remains explicit in the report.
    """

    mismatches: list[str] = []
    expected = {
        "adapter_type": manifest.runtime.transport,
        "adapter_entrypoint": manifest.integration.adapter_entrypoint,
        "isolation_mode": manifest.integration.isolation_mode,
        "sandbox_profile": manifest.sandbox.profile,
        "team_id": _manifest_team_id(manifest),
        "version_pin": manifest.metadata.version_pin,
        "source_revision": manifest.metadata.source_revision,
        "model_mode": manifest.model_mode,
        "model_profile_id": manifest.model_profile_id,
    }
    expected_source = (
        None
        if str(manifest.metadata.source_repo or "").strip().lower() == "local"
        else _normalise_source(manifest.metadata.source_repo)
    )
    actual_source_raw = row.get("source_repo")
    actual_source = (
        None
        if not str(actual_source_raw or "").strip()
        else _normalise_source(actual_source_raw)
    )
    if actual_source != expected_source:
        mismatches.append("source_repo")
    for field, expected_value in expected.items():
        actual_value = row.get(field)
        if field in {"version_pin", "source_revision"}:
            expected_value = str(expected_value or "") or None
            actual_value = str(actual_value or "") or None
        elif field in {"model_profile_id", "team_id"}:
            expected_value = str(expected_value) if expected_value is not None else None
            actual_value = str(actual_value) if actual_value is not None else None
        elif field == "model_mode":
            expected_value = str(expected_value or "none")
            actual_value = str(actual_value or "none")
        else:
            expected_value = str(expected_value or "")
            actual_value = str(actual_value or "")
        if actual_value != expected_value:
            mismatches.append(field)

    expected_capabilities = {capability.name for capability in manifest.capabilities}
    actual_capabilities = {
        str(name) for name in row.get("capability_names") or [] if str(name).strip()
    }
    missing_capabilities = expected_capabilities - actual_capabilities
    if missing_capabilities:
        mismatches.append("capability_names")

    status = str(row.get("status") or "").upper()
    if status in {"ACTIVE", "DRAINING"}:
        # A live ACTIVE row must point at the immutable control-plane records;
        # a YAML declaration or a source pin alone is not activation evidence.
        for field in ("active_shell_version_id", "active_adapter_id", "active_skill_bundle_id"):
            if not row.get(field):
                mismatches.append(field)
    return sorted(set(mismatches))


def _blocked_live(reason: str, *, url_configured: bool = False) -> dict[str, Any]:
    return {
        "schema_version": CHECK_SCHEMA,
        "mode": "live",
        "status": "blocked",
        "reason": reason,
        "url_configured": url_configured,
        "licence_metadata_is_gate": False,
        "scope": "read-only default-worker binding reconciliation; no database mutation",
    }


def _live_reconcile(
    *,
    workers_dir: Path,
    url: str,
    api_key: str,
    timeout: float,
) -> dict[str, Any]:
    """Reconcile checked-in defaults with the persisted worker listing."""

    if not url.strip():
        return _blocked_live("missing live configuration: orchestrator URL")
    manifests: dict[str, WorkerManifest] = {}
    try:
        for path in sorted(workers_dir.glob("*.yaml")):
            manifest = WorkerManifest.model_validate(_load_yaml(path))
            manifests[manifest.metadata.id] = manifest
    except (OSError, ValueError, yaml.YAMLError, ValidationError) as exc:
        return _blocked_live(f"checked-in worker manifest unavailable: {type(exc).__name__}", url_configured=True)

    headers = {"X-API-Key": api_key} if api_key.strip() else {}
    try:
        response = httpx.get(
            f"{url.rstrip('/')}/capabilities/workers",
            headers=headers,
            params={"_aiat_release_check": "1"},
            timeout=timeout,
        )
        response.raise_for_status()
        payload = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        return _blocked_live(
            f"worker registry endpoint unavailable: {type(exc).__name__}",
            url_configured=True,
        )
    if not isinstance(payload, list) or not all(isinstance(row, dict) for row in payload):
        return _blocked_live("orchestrator returned an invalid worker registry listing", url_configured=True)

    rows_by_name: dict[str, dict[str, Any]] = {}
    duplicate_names: list[str] = []
    for row in payload:
        name = str(row.get("name") or "").strip()
        if not name:
            duplicate_names.append("<blank>")
            continue
        if name in rows_by_name:
            duplicate_names.append(name)
        rows_by_name[name] = row

    missing = sorted(set(manifests) - set(rows_by_name))
    mismatches: dict[str, list[str]] = {}
    for worker_id, manifest in manifests.items():
        row = rows_by_name.get(worker_id)
        if row is not None:
            fields = _binding_mismatches(manifest, row)
            if fields:
                mismatches[worker_id] = fields
    active_untracked = sorted(
        str(row.get("name"))
        for name, row in rows_by_name.items()
        if name not in manifests and str(row.get("status") or "").upper() in {"ACTIVE", "DRAINING"}
    )
    errors = []
    if duplicate_names:
        errors.append(f"duplicate or blank persisted worker names: {sorted(set(duplicate_names))}")
    if missing:
        errors.append(f"default workers missing from persisted registry: {missing}")
    if mismatches:
        errors.append(f"default worker binding mismatches: {mismatches}")
    return {
        "schema_version": CHECK_SCHEMA,
        "mode": "live",
        "status": "fail" if errors else "pass",
        "url_configured": True,
        "worker_count": len(payload),
        "default_worker_count": len(manifests),
        "matched_count": len(manifests) - len(missing) - len(mismatches),
        "active_default_worker_count": sum(
            str(rows_by_name[worker_id].get("status") or "").upper() in {"ACTIVE", "DRAINING"}
            for worker_id in manifests
            if worker_id in rows_by_name
        ),
        "missing_worker_count": len(missing),
        "mismatch_count": len(mismatches),
        "untracked_active_worker_count": len(active_untracked),
        "duplicate_name_count": len(set(duplicate_names)),
        "errors": errors,
        "warnings": (
            [f"active custom workers are outside checked-in default scope: {active_untracked}"]
            if active_untracked
            else []
        ),
        "licence_metadata_is_gate": False,
        "scope": "read-only default-worker binding reconciliation; no database mutation",
    }


def reconcile(
    *,
    workers_dir: Path = DEFAULT_WORKERS_DIR,
    company_manifest: Path = DEFAULT_COMPANY_MANIFEST,
    provenance_path: Path = DEFAULT_PROVENANCE,
    image_provenance_path: Path = DEFAULT_IMAGE_PROVENANCE,
    compose_path: Path = DEFAULT_COMPOSE,
    security_evidence_path: Path = DEFAULT_SECURITY_EVIDENCE,
    notices_path: Path = DEFAULT_NOTICES,
) -> dict[str, Any]:
    """Return a deterministic static reconciliation report."""

    errors: list[str] = []
    warnings: list[str] = []
    pending: list[dict[str, Any]] = []
    manifests: list[tuple[Path, WorkerManifest]] = []
    worker_ids: set[str] = set()
    security_evidence: dict[str, dict[str, Any]] = {}

    _check_runtime_catalog(errors)

    try:
        provenance = _load_yaml(provenance_path)
        _check_policy(provenance, notices_path.read_text(encoding="utf-8"), errors)
    except (OSError, ValueError, yaml.YAMLError) as exc:
        errors.append(f"could not load provenance/notice metadata: {exc}")
        provenance = {}

    if security_evidence_path.exists():
        security_evidence = _security_evidence_index(security_evidence_path, errors)

    for path in sorted(workers_dir.glob("*.yaml")):
        try:
            raw = _load_yaml(path)
            manifest = WorkerManifest.model_validate(raw)
        except (OSError, ValueError, yaml.YAMLError, ValidationError) as exc:
            errors.append(f"{path.name}: manifest validation failed: {exc}")
            continue
        worker_id = manifest.metadata.id
        if worker_id != path.stem:
            errors.append(f"{path.name}: metadata.id {worker_id!r} must match filename stem")
        if worker_id in worker_ids:
            errors.append(f"duplicate worker manifest id {worker_id!r}")
        worker_ids.add(worker_id)
        manifests.append((path, manifest))

        definition = RUNTIME_CATALOG.get(manifest.runtime_tier)
        if definition is None:
            errors.append(f"{path.name}: runtime tier {manifest.runtime_tier!r} is not in the runtime catalogue")
        else:
            transport = manifest.runtime.transport
            if transport not in SUPPORTED_TRANSPORTS:
                errors.append(f"{path.name}: unsupported transport {transport!r}")
            elif transport not in definition.supported_transports:
                errors.append(
                    f"{path.name}: transport {transport!r} is not supported by runtime tier "
                    f"{manifest.runtime_tier!r}"
                )
            isolation_mode = manifest.integration.isolation_mode
            if isolation_mode not in definition.supported_isolation_modes:
                errors.append(
                    f"{path.name}: isolation_mode {isolation_mode!r} disagrees with runtime tier "
                    f"{manifest.runtime_tier!r}"
                )
        preferred_runtime = str(manifest.runtime.adapter_config.get("preferred_runtime") or "")
        if preferred_runtime and preferred_runtime != manifest.runtime_tier:
            errors.append(
                f"{path.name}: preferred_runtime {preferred_runtime!r} disagrees with "
                f"runtime_tier {manifest.runtime_tier!r}"
            )

        source_repo = str(manifest.metadata.source_repo or "").strip()
        if source_repo.lower() == "local":
            if manifest.source_provenance:
                warnings.append(f"{path.name}: local worker carries external provenance metadata")
        elif source_repo:
            if not manifest.metadata.version_pin:
                errors.append(f"{path.name}: external worker requires metadata.version_pin")
            if not manifest.metadata.source_revision:
                errors.append(f"{path.name}: external worker requires metadata.source_revision")
            source_provenance = manifest.source_provenance
            if not source_provenance:
                errors.append(f"{path.name}: external worker requires source_provenance metadata")
            else:
                if not source_provenance.get("source_provider"):
                    errors.append(f"{path.name}: source_provenance.source_provider is required")
                pin_fields = ("exact_release", "commit_sha", "package_version", "oci_image_digest")
                if not any(source_provenance.get(field) for field in pin_fields):
                    errors.append(f"{path.name}: source_provenance needs an immutable release/commit/package/image pin")
                if "security_scan_status" not in source_provenance:
                    errors.append(f"{path.name}: source_provenance.security_scan_status is required")
                exact_release = source_provenance.get("exact_release")
                if (
                    _is_concrete_version(exact_release)
                    and _is_concrete_version(manifest.metadata.version_pin)
                    and _normalise_version(exact_release) != _normalise_version(manifest.metadata.version_pin)
                ):
                    errors.append(
                        f"{path.name}: source_provenance.exact_release {exact_release!r} disagrees with "
                        f"metadata.version_pin {manifest.metadata.version_pin!r}"
                    )
                transport_type = source_provenance.get("transport_type")
                if transport_type and transport_type != manifest.runtime.transport:
                    errors.append(
                        f"{path.name}: source_provenance.transport_type {transport_type!r} disagrees with "
                        f"runtime.transport {manifest.runtime.transport!r}"
                    )
            matching_component = next(
                (
                    item
                    for item in provenance.get("components") or []
                    if isinstance(item, dict)
                    and _normalise_source(item.get("source")) == _normalise_source(source_repo)
                ),
                None,
            )
            if matching_component is None:
                errors.append(f"{path.name}: source_repo is absent from third-party provenance inventory")
            else:
                inventory_version = matching_component.get("version")
                manifest_version = manifest.metadata.version_pin
                if (
                    _is_concrete_version(inventory_version)
                    and _is_concrete_version(manifest_version)
                    and _normalise_version(inventory_version) != _normalise_version(manifest_version)
                ):
                    errors.append(
                        f"{path.name}: manifest version_pin {manifest_version!r} disagrees with "
                        f"inventory version {inventory_version!r}"
                    )
            scan_status = str((manifest.source_provenance or {}).get("security_scan_status") or "pending")
            if scan_status.lower() != "passed":
                pending_row: dict[str, Any] = {
                    "worker": worker_id,
                    "evidence": "security_scan_status",
                    "status": scan_status,
                }
                evidence_ref = str(
                    (manifest.source_provenance or {}).get("security_scan_evidence_ref") or ""
                ).strip()
                if evidence_ref:
                    resolved_ref = _resolve_security_evidence_ref(evidence_ref)
                    if resolved_ref is None:
                        errors.append(f"{path.name}: security_scan_evidence_ref is invalid: {evidence_ref!r}")
                    else:
                        evidence_path, scan_id = resolved_ref
                        if evidence_path != security_evidence_path.resolve():
                            errors.append(
                                f"{path.name}: security_scan_evidence_ref must point to "
                                f"{security_evidence_path.relative_to(PROGRAMME_ROOT)}"
                            )
                        scan = security_evidence.get(scan_id)
                        if scan is None:
                            errors.append(f"{path.name}: security scan evidence {scan_id!r} is missing")
                        else:
                            targets = {str(item) for item in scan.get("target_workers") or []}
                            if worker_id not in targets:
                                errors.append(
                                    f"{path.name}: security scan evidence {scan_id!r} does not target {worker_id!r}"
                                )
                            if str(scan.get("source_repo") or "") != source_repo:
                                errors.append(
                                    f"{path.name}: security scan evidence {scan_id!r} source_repo disagrees"
                                )
                            if str(scan.get("source_revision") or "") != str(manifest.metadata.source_revision or ""):
                                errors.append(
                                    f"{path.name}: security scan evidence {scan_id!r} source_revision disagrees"
                                )
                            pending_row["security_scan_evidence_ref"] = evidence_ref
                            pending_row["finding_count"] = int(scan.get("finding_count") or 0)
                            pending_row["evidence_status"] = str(scan.get("status") or "")
                pending.append(pending_row)
        else:
            errors.append(f"{path.name}: metadata.source_repo is required")

    if not manifests:
        errors.append(f"no worker manifests found in {workers_dir}")

    try:
        _check_company_manifest(_load_yaml(company_manifest), worker_ids, errors)
    except (OSError, ValueError, yaml.YAMLError) as exc:
        errors.append(f"could not load company manifest: {exc}")
    try:
        _check_compose_links(
            manifests,
            _load_yaml(compose_path),
            _load_yaml(image_provenance_path),
            errors,
        )
    except (OSError, ValueError, yaml.YAMLError) as exc:
        errors.append(f"could not load Compose/image metadata: {exc}")

    runtime_counts: dict[str, int] = {}
    transport_counts: dict[str, int] = {}
    for _, manifest in manifests:
        runtime_counts[manifest.runtime_tier] = runtime_counts.get(manifest.runtime_tier, 0) + 1
        transport_counts[manifest.runtime.transport] = transport_counts.get(manifest.runtime.transport, 0) + 1
    return {
        "status": "pass" if not errors else "fail",
        "worker_count": len(manifests),
        "runtime_counts": dict(sorted(runtime_counts.items())),
        "transport_counts": dict(sorted(transport_counts.items())),
        "runtime_availability": _runtime_availability(),
        "pending_evidence": pending,
        "warnings": sorted(warnings),
        "errors": sorted(errors),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workers-dir", type=Path, default=DEFAULT_WORKERS_DIR)
    parser.add_argument("--company-manifest", type=Path, default=DEFAULT_COMPANY_MANIFEST)
    parser.add_argument("--provenance", type=Path, default=DEFAULT_PROVENANCE)
    parser.add_argument("--image-provenance", type=Path, default=DEFAULT_IMAGE_PROVENANCE)
    parser.add_argument("--compose", type=Path, default=DEFAULT_COMPOSE)
    parser.add_argument("--security-evidence", type=Path, default=DEFAULT_SECURITY_EVIDENCE)
    parser.add_argument("--notices", type=Path, default=DEFAULT_NOTICES)
    parser.add_argument("--live", action="store_true", help="reconcile checked-in defaults with the running worker registry")
    parser.add_argument(
        "--url",
        default=os.environ.get("AIAT_ORCHESTRATOR_URL", os.environ.get("ORCHESTRATOR_API_URL", "")),
        help="orchestrator base URL (or AIAT_ORCHESTRATOR_URL/ORCHESTRATOR_API_URL)",
    )
    parser.add_argument(
        "--api-key",
        default=os.environ.get(
            "AIAT_OPERATOR_API_KEY",
            os.environ.get("AIAT_API_KEY", os.environ.get("MAS_API_KEY", "")),
        ),
        help="optional API key; never included in the report",
    )
    parser.add_argument("--timeout", type=float, default=15.0, help="live request timeout in seconds")
    parser.add_argument("--json", action="store_true", help="print the complete report as JSON")
    args = parser.parse_args(argv)

    report = reconcile(
        workers_dir=args.workers_dir,
        company_manifest=args.company_manifest,
        provenance_path=args.provenance,
        image_provenance_path=args.image_provenance,
        compose_path=args.compose,
        security_evidence_path=args.security_evidence,
        notices_path=args.notices,
    )
    if args.live:
        live = _live_reconcile(
            workers_dir=args.workers_dir,
            url=args.url,
            api_key=args.api_key,
            timeout=args.timeout,
        )
        if report["status"] != "pass" and live.get("status") == "pass":
            live = {
                **live,
                "status": "fail",
                "errors": ["static reconciliation failed; live binding cannot override it"],
            }
        report = {**report, "mode": "live", "live": live, "status": live.get("status", "fail")}
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(
            "worker-reconciliation: "
            f"{report['status']} ({report['worker_count']} manifests, "
            f"{len(report['pending_evidence'])} pending evidence records)"
        )
        print(f"worker-reconciliation: runtimes={report['runtime_counts']} transports={report['transport_counts']}")
        for warning in report["warnings"]:
            print(f"worker-reconciliation: warning: {warning}")
        for pending in report["pending_evidence"]:
            print(
                "worker-reconciliation: pending: "
                f"{pending['worker']} {pending['evidence']}={pending['status']}"
            )
        for error in report["errors"]:
            print(f"worker-reconciliation: error: {error}", file=sys.stderr)
        if args.live:
            print(
                "worker-reconciliation: live="
                f"{report['live'].get('status')} "
                f"matched={report['live'].get('matched_count', 0)}/"
                f"{report['live'].get('default_worker_count', 0)}"
            )
    if args.live and report.get("live", {}).get("status") == "blocked":
        return 2
    return 0 if report["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())

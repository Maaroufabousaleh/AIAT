"""Reconcile team-runner agent declarations with worker manifests.

Team YAMLs are executable configuration, so every agent must identify the
checked-in worker manifest that governs its identity.  This module is a
read-only declaration check: it never resolves a missing reference by agent
name, registers a worker, activates a worker, or changes a manifest.

Licence and resource-restriction fields are not part of this identity
reconciliation and remain informational metadata elsewhere.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

import yaml  # type: ignore[import-untyped]

if TYPE_CHECKING:
    from pathlib import Path

TEAM_MANIFEST_REFS_SCHEMA = "aiat.team-worker-manifest-reconciliation.v1"


def _load_mapping(path: Path) -> Mapping[str, Any]:
    value = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(value, Mapping):
        raise ValueError("expected a YAML mapping")
    return value


def _agent_rows(team: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    rows: list[Mapping[str, Any]] = []
    admin = team.get("admin")
    if isinstance(admin, Mapping):
        rows.append(admin)
    workers = team.get("workers")
    if isinstance(workers, list):
        rows.extend(row for row in workers if isinstance(row, Mapping))
    return rows


def reconcile_team_worker_manifest_refs(
    *,
    teams_dir: Path,
    workers_dir: Path,
) -> dict[str, Any]:
    """Return a deterministic report for all team/worker manifest bindings."""

    errors: list[str] = []
    worker_manifest_ids: dict[str, str] = {}
    worker_files = sorted(workers_dir.glob("*.yaml")) if workers_dir.is_dir() else []
    if not workers_dir.is_dir():
        errors.append("workers directory is missing")
    for path in worker_files:
        try:
            manifest = _load_mapping(path)
        except (OSError, ValueError, yaml.YAMLError) as exc:
            errors.append(f"{path.name}: invalid worker manifest ({type(exc).__name__})")
            continue
        metadata = manifest.get("metadata")
        worker_id = str(metadata.get("id") or "").strip() if isinstance(metadata, Mapping) else ""
        if not worker_id:
            errors.append(f"{path.name}: metadata.id is required")
            continue
        previous = worker_manifest_ids.get(worker_id)
        if previous is not None:
            errors.append(f"duplicate worker manifest ID {worker_id!r}: {previous} and {path.name}")
            continue
        worker_manifest_ids[worker_id] = path.name

    team_rows: list[dict[str, Any]] = []
    seen_agents: dict[str, str] = {}
    team_files = sorted(teams_dir.glob("*.yaml")) if teams_dir.is_dir() else []
    if not teams_dir.is_dir():
        errors.append("teams directory is missing")
    for path in team_files:
        try:
            team = _load_mapping(path)
        except (OSError, ValueError, yaml.YAMLError) as exc:
            errors.append(f"{path.name}: invalid team config ({type(exc).__name__})")
            continue
        team_id = str(team.get("team_id") or "").strip()
        if not team_id:
            errors.append(f"{path.name}: team_id is required")
        agents = _agent_rows(team)
        if not agents:
            errors.append(f"{path.name}: admin must be a mapping and workers must be a list")
        row_errors: list[str] = []
        for agent in agents:
            agent_id = str(agent.get("agent_id") or "").strip()
            manifest_ref = str(agent.get("worker_manifest_ref") or "").strip()
            label = f"{path.name}:{agent_id or '<missing-agent-id>'}"
            if not agent_id:
                row_errors.append(f"{label}: agent_id is required")
                continue
            previous = seen_agents.get(agent_id)
            if previous is not None:
                row_errors.append(f"{label}: duplicate agent_id also declared at {previous}")
            else:
                seen_agents[agent_id] = label
            if not manifest_ref:
                row_errors.append(f"{label}: worker_manifest_ref is required")
            elif manifest_ref != agent_id:
                row_errors.append(
                    f"{label}: worker_manifest_ref {manifest_ref!r} must equal agent_id {agent_id!r}"
                )
            elif manifest_ref not in worker_manifest_ids:
                row_errors.append(f"{label}: worker manifest {manifest_ref!r} was not found")
        errors.extend(row_errors)
        team_rows.append(
            {
                "team_id": team_id or None,
                "file": path.name,
                "agent_count": len(agents),
                "error_count": len(row_errors),
            }
        )

    return {
        "schema_version": TEAM_MANIFEST_REFS_SCHEMA,
        "status": "pass" if not errors else "fail",
        "licence_metadata_is_gate": False,
        "teams_directory": str(teams_dir),
        "workers_directory": str(workers_dir),
        "team_count": len(team_rows),
        "worker_manifest_count": len(worker_manifest_ids),
        "agent_count": sum(int(row["agent_count"]) for row in team_rows),
        "teams": team_rows,
        "errors": sorted(errors),
        "scope": "read-only team-runner declaration to checked-in worker-manifest identity reconciliation; no registration, activation, provisioning, or licence decision",
    }


__all__ = [
    "TEAM_MANIFEST_REFS_SCHEMA",
    "reconcile_team_worker_manifest_refs",
]

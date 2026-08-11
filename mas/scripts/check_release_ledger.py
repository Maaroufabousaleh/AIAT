"""Assemble a secret-safe machine-readable AIAT release-evidence ledger.

The checker runs the existing bounded verifiers listed in
``docs/provenance/release_ledger.yaml``. It does not replace their individual
contracts, and it never upgrades missing live evidence into a pass. The
default profile is suitable for CI and evaluates static/fixture evidence;
``--live`` adds every configured live probe and classifies unavailable Docker,
provider, or API state as ``blocked``.

Each child verifier is bounded by a timeout (60 seconds for live checks by
default, 120 seconds for static checks). Operators may override the bound with
``AIAT_RELEASE_CHECK_TIMEOUT_SECONDS``; invalid, non-positive, or excessive
values fall back to the safe default/cap. A timed-out live verifier is
recorded as ``blocked`` rather than being treated as a pass.

The release decision is intentionally stricter than a static pass: it remains
``NO-RELEASE`` unless the live profile is included, every required check passes,
no worker evidence is pending, and the worktree is clean.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import re
import shutil
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

MAS_ROOT = Path(__file__).resolve().parents[1]
INVENTORY_PATH = MAS_ROOT / "docs" / "provenance" / "release_ledger.yaml"
SCHEMA = "aiat.release-ledger.v1"
_SECRET_KEY_RE = re.compile(r"(?:secret|password|token|api[_-]?key|access[_-]?key|credential|authorization)", re.I)
_SECRET_ASSIGNMENT_RE = re.compile(
    r"(?i)(secret|password|token|api[_-]?key|access[_-]?key|credential|authorization)\s*=\s*[^\s,;]+"
)
_SAFE_STATUS = {"pass", "passed", "healthy", "observed", "clear"}
_DEFAULT_CHECK_TIMEOUT_SECONDS = 120.0
_DEFAULT_LIVE_CHECK_TIMEOUT_SECONDS = 60.0
_MAX_CHECK_TIMEOUT_SECONDS = 600.0
_DEFAULT_CHECK_WORKERS = 4
_MAX_CHECK_WORKERS = 16


@dataclass(frozen=True, slots=True)
class CheckSpec:
    check_id: str
    category: str
    script: str
    args: tuple[str, ...]
    live_args: tuple[str, ...] = ()


def _load_inventory(path: Path = INVENTORY_PATH) -> tuple[dict[str, Any], list[CheckSpec]]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict) or raw.get("schema_version") != "1":
        raise ValueError("release_ledger.yaml must declare schema_version '1'")
    if raw.get("programme_scope") != "personal-internal-only":
        raise ValueError("release ledger inventory must remain personal-internal-only")
    checks: list[CheckSpec] = []
    seen: set[str] = set()
    for row in raw.get("checks") or []:
        if not isinstance(row, dict):
            raise ValueError("release ledger checks must be mappings")
        check_id = str(row.get("id") or "").strip()
        script = str(row.get("script") or "").strip()
        if not check_id or check_id in seen or not script:
            raise ValueError("release ledger check IDs must be unique and scripts non-blank")
        script_path = (MAS_ROOT / script).resolve()
        if MAS_ROOT not in script_path.parents or script_path.suffix != ".py":
            raise ValueError(f"release ledger script escapes repository: {script}")
        seen.add(check_id)
        checks.append(
            CheckSpec(
                check_id=check_id,
                category=str(row.get("category") or "static"),
                script=script,
                args=tuple(str(value) for value in row.get("args") or []),
                live_args=tuple(str(value) for value in row.get("live_args") or []),
            )
        )
    if not checks:
        raise ValueError("release ledger inventory must define checks")
    return raw, checks


def _git_metadata() -> dict[str, Any]:
    def run(*args: str) -> str:
        result = subprocess.run(
            ["git", *args],
            cwd=MAS_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        return result.stdout.strip() if result.returncode == 0 else ""

    porcelain = run("status", "--porcelain", "--untracked-files=all")
    return {
        "revision": run("rev-parse", "HEAD") or None,
        "branch": run("branch", "--show-current") or None,
        "clean": not bool(porcelain),
        "changed_path_count": len(porcelain.splitlines()) if porcelain else 0,
    }


def _redact(value: Any, *, key: str = "") -> Any:
    if _SECRET_KEY_RE.search(key):
        return "[redacted]"
    if isinstance(value, dict):
        return {str(name): _redact(item, key=str(name)) for name, item in value.items()}
    if isinstance(value, list):
        return [_redact(item, key=key) for item in value[:50]]
    if isinstance(value, str):
        # Child checkers should already avoid secrets; this defensive pass also
        # prevents accidental credential-shaped values in diagnostics.
        return _SECRET_ASSIGNMENT_RE.sub(r"\1=[redacted]", value[:2_000])
    return value


def _parse_payload(stdout: str) -> dict[str, Any] | None:
    text = stdout.strip()
    if not text:
        return None
    candidates = [text]
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    candidates.extend(reversed(lines))
    for candidate in candidates:
        try:
            value = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    return None


def _safe_summary(payload: dict[str, Any] | None, stdout: str, stderr: str) -> dict[str, Any]:
    if payload is None:
        lines = [line.strip() for line in (stdout or stderr).splitlines() if line.strip()]
        return {"summary": lines[-1][:500] if lines else None}
    allowed = {
        "status",
        "mode",
        "reason",
        "errors",
        "warnings",
        "pending_evidence",
        "worker_count",
        "default_worker_count",
        "matched_count",
        "active_default_worker_count",
        "missing_worker_count",
        "mismatch_count",
        "untracked_active_worker_count",
        "duplicate_name_count",
        "runtime_counts",
        "transport_counts",
        "manifest_tool_count",
        "prompt_count",
        "registered_tool_count",
        "path_count",
        "model_count",
        "operation_count",
        "counts",
        "passed",
        "total",
        "budget",
        "family_counts",
        "family_budgets",
        "declared_label_inventory",
        "label_inventory",
        "label_policies",
        "synthetic_project_count",
        "project_id_label_present",
        "url_configured",
        "coverage",
        "notice_codes",
        "slo_status",
        "capacity_status",
        "profile",
        "declared_dependencies",
        "locked_versions",
        "lock_profile",
        "dockerfile_installs_profile",
        "external_worker_count",
        "manifest_digest",
        "tracked_input_count",
        "available_tool_count",
        "configured_environment_input_count",
        "pin_count",
        "locked_count",
        "unavailable_count",
        "registry_model_count",
        "profile_count",
        "profile_version_count",
        "covered_profile_version_count",
        "profile_pending_model_count",
        "profile_coverage",
        "duplicate_profile_bindings",
        "git",
    }
    summary = {key: payload[key] for key in allowed if key in payload}
    if isinstance(payload.get("findings"), list):
        summary["finding_count"] = len(payload["findings"])
    native_release = payload.get("native_release")
    if isinstance(native_release, dict):
        native_blockers = native_release.get("blockers")
        native_summary: dict[str, Any] = {
            "status": native_release.get("status"),
            "blockers": [
                str(blocker)[:300]
                for blocker in (native_blockers[:20] if isinstance(native_blockers, list) else [])
                if isinstance(blocker, str)
            ],
        }
        native_platform = native_release.get("platform")
        if isinstance(native_platform, dict):
            native_summary["platform"] = {
                key: native_platform[key]
                for key in ("system", "kernel", "native_linux")
                if key in native_platform
            }
        native_docker = native_release.get("docker")
        if isinstance(native_docker, dict):
            native_summary["docker"] = {
                key: native_docker[key]
                for key in (
                    "engine_available",
                    "compose_v2_available",
                    "runtimes_metadata_available",
                    "runsc_registered",
                )
                if key in native_docker
            }
        native_refs = native_release.get("image_refs")
        if isinstance(native_refs, list):
            native_summary["image_refs"] = [
                {
                    key: item[key]
                    for key in ("name", "configured", "digest_pinned")
                    if key in item
                }
                for item in native_refs[:20]
                if isinstance(item, dict)
            ]
        summary["native_release"] = native_summary
    live = payload.get("live")
    if isinstance(live, dict):
        summary["live"] = {
            key: live[key]
            for key in (
                "status",
                "reason",
                "errors",
                "warnings",
                "total",
                "budget",
                "worker_count",
                "default_worker_count",
                "matched_count",
                "active_default_worker_count",
                "missing_worker_count",
                "mismatch_count",
                "untracked_active_worker_count",
                "duplicate_name_count",
                "family_counts",
                "family_budgets",
                "declared_label_inventory",
                "label_inventory",
                "label_policies",
                "project_id_label_present",
                "url_configured",
                "manifest_digest",
                "tracked_input_count",
                "available_tool_count",
                "configured_environment_input_count",
                "git",
            )
            if key in live
        }
        if isinstance(live.get("containers"), list):
            summary["live"]["container_count"] = len(live["containers"])
    return _redact(summary)


def _status_for(returncode: int, payload: dict[str, Any] | None, *, live: bool = False) -> str:
    effective = payload.get("live") if live and isinstance(payload, dict) else payload
    if not isinstance(effective, dict):
        effective = payload
    if returncode == 2 or (effective and str(effective.get("status", "")).lower() == "blocked"):
        return "blocked"
    if returncode != 0:
        return "fail"
    if effective:
        raw = str(effective.get("status", "")).lower()
        if raw in {"fail", "failed", "error"} or effective.get("passed") is False:
            return "fail"
    return "pass"


def _check_timeout_seconds(*, live: bool) -> float:
    """Return a bounded child-check timeout without trusting environment input."""

    default = _DEFAULT_LIVE_CHECK_TIMEOUT_SECONDS if live else _DEFAULT_CHECK_TIMEOUT_SECONDS
    raw = os.getenv("AIAT_RELEASE_CHECK_TIMEOUT_SECONDS", "").strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError:
        return default
    if value <= 0:
        return default
    return min(value, _MAX_CHECK_TIMEOUT_SECONDS)


def _check_worker_count() -> int:
    """Return a bounded number of concurrent child-check workers."""

    raw = os.getenv("AIAT_RELEASE_LEDGER_WORKERS", "").strip()
    if not raw:
        return _DEFAULT_CHECK_WORKERS
    try:
        value = int(raw)
    except ValueError:
        return _DEFAULT_CHECK_WORKERS
    if value <= 0:
        return _DEFAULT_CHECK_WORKERS
    return min(value, _MAX_CHECK_WORKERS)


def _run_checks(specs: list[CheckSpec], *, live: bool) -> list[dict[str, Any]]:
    """Run independent child verifiers concurrently while preserving inventory order."""

    if not specs:
        return []
    with ThreadPoolExecutor(
        max_workers=min(_check_worker_count(), len(specs)),
        thread_name_prefix="aiat-release-check",
    ) as executor:
        # executor.map preserves input order, keeping reports deterministic even
        # though independent child processes finish at different times.
        return list(executor.map(lambda spec: _run_check(spec, live=live), specs))


def _run_check(spec: CheckSpec, *, live: bool) -> dict[str, Any]:
    args = spec.live_args if live else spec.args
    command = [sys.executable, spec.script, *args]
    timeout = _check_timeout_seconds(live=live)
    try:
        result = subprocess.run(
            command,
            cwd=MAS_ROOT,
            env=os.environ.copy(),
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return {
            "id": f"{spec.check_id}:live" if live else spec.check_id,
            "base_id": spec.check_id,
            "category": spec.category,
            "mode": "live" if live else "static",
            "status": "blocked" if live else "fail",
            "exit_code": 2 if live else 1,
            "pending_evidence_count": 0,
            "summary": {
                "status": "blocked" if live else "fail",
                "reason": f"checker timed out after {timeout:g}s",
                "timeout_seconds": timeout,
            },
        }
    payload = _parse_payload(result.stdout)
    pending = payload.get("pending_evidence") if isinstance(payload, dict) else None
    return {
        "id": f"{spec.check_id}:live" if live else spec.check_id,
        "base_id": spec.check_id,
        "category": spec.category,
        "mode": "live" if live else "static",
        "status": _status_for(result.returncode, payload, live=live),
        "exit_code": result.returncode,
        "pending_evidence_count": len(pending) if isinstance(pending, list) else 0,
        "summary": _safe_summary(payload, result.stdout, result.stderr),
    }


def build_report(*, include_live: bool = False) -> dict[str, Any]:
    inventory, specs = _load_inventory()
    checks = _run_checks(specs, live=False)
    if include_live:
        checks.extend(_run_checks([spec for spec in specs if spec.live_args], live=True))
    counts = {status: sum(row["status"] == status for row in checks) for status in ("pass", "blocked", "fail")}
    pending_count = sum(int(row["pending_evidence_count"]) for row in checks)
    overall_status = "fail" if counts["fail"] else ("blocked" if counts["blocked"] else "pass")
    git = _git_metadata()
    reasons: list[str] = []
    if not include_live:
        reasons.append("live profile was not included")
    if counts["blocked"]:
        reasons.append(f"{counts['blocked']} check(s) are externally blocked")
    if pending_count:
        reasons.append(f"{pending_count} pending evidence item(s) remain")
    if not git["clean"]:
        reasons.append("worktree is dirty")
    release = "RELEASE" if include_live and overall_status == "pass" and pending_count == 0 and git["clean"] else "NO-RELEASE"
    return {
        "schema_version": SCHEMA,
        "checked_at": datetime.now(tz=UTC).isoformat(),
        "profile": "live" if include_live else "static",
        "status": overall_status,
        "release_decision": release,
        "decision_reasons": reasons,
        "counts": {**counts, "total": len(checks)},
        "pending_evidence_count": pending_count,
        "git": git,
        "environment": {
            "python": platform.python_version(),
            "platform": platform.platform(aliased=True),
            "docker_cli_available": shutil.which("docker") is not None,
        },
        "policy": {
            "programme_scope": inventory["programme_scope"],
            "licence_metadata_is_gate": False,
            "blocked_live_evidence_is_pass": False,
        },
        "checks": checks,
        "scope": "bounded release-evidence aggregation; no deployment mutation or credential output",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="include configured live probes")
    parser.add_argument("--json", action="store_true", help="emit the full machine-readable report")
    args = parser.parse_args(argv)
    try:
        report = build_report(include_live=args.live)
    except (OSError, ValueError, yaml.YAMLError) as exc:
        if args.json:
            print(json.dumps({"schema_version": SCHEMA, "status": "fail", "reason": str(exc)}))
        else:
            print(f"release-ledger: FAIL — {exc}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(report, sort_keys=True, indent=2))
    else:
        print(
            f"release-ledger: {report['release_decision']} — "
            f"status={report['status']} checks={report['counts']['total']} "
            f"pass={report['counts']['pass']} blocked={report['counts']['blocked']} "
            f"fail={report['counts']['fail']}"
        )
        for reason in report["decision_reasons"]:
            print(f"  - {reason}")
    if report["status"] == "blocked":
        return 2
    return 0 if report["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())

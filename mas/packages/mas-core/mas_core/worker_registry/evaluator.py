"""Repository evaluation engine for guarded worker adoption."""

from __future__ import annotations

import json
import logging
import re
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

import yaml

from mas_core.protocols.worker_manifest import WorkerManifest

if TYPE_CHECKING:
    from mas_core.memory.storage import AgentStorage

logger = logging.getLogger(__name__)

EVALUATOR_VERSION = "1.1.0"

DEFAULT_GUARDED_CHECKS = [
    "provenance",
    "version_pin",
    "manifest_validation",
    "trufflehog",
    "semgrep",
    "compatibility",
    "sandbox_profile",
    "budget_latency",
    "approval",
]

VALID_SANDBOX_PROFILES = {"standard", "restricted", "gvisor", "firecracker"}
HARDENED_SANDBOX_PROFILES = {"gvisor", "firecracker"}

COMPATIBLE_LICENSES = {
    "mit",
    "apache-2.0",
    "apache 2.0",
    "bsd-2-clause",
    "bsd-3-clause",
    "isc",
    "unlicense",
    "bsd",
    "0bsd",
}

REJECTED_LICENSES = {
    "gpl-2.0",
    "gpl-3.0",
    "agpl-3.0",
    "gpl",
    "agpl",
    "sspl",
    "epl",
}

ENTRYPOINT_PATTERNS = [
    r"def\s+main\s*\(",
    r"def\s+run\s*\(",
    r"def\s+execute\s*\(",
    r"def\s+handle\s*\(",
    r"class\s+\w+(Agent|Worker|Handler|Server|Service)",
    r"app\s*=\s*FastAPI",
    r"app\s*=\s*Flask",
    r"if\s+__name__\s*==\s*['\"]__main__['\"]",
]

SECRET_PATTERNS = [
    r"(?i)(api[_-]?key|secret|token|password)\s*[:=]\s*['\"][A-Za-z0-9+/=]{16,}['\"]",
    r"(?i)(AKIA|GH[PS]_|sk-)[A-Za-z0-9]{20,}",
]


async def evaluate_repository(
    *,
    worker_id: UUID,
    source_repo: str,
    storage: AgentStorage,
    checks: list[str] | None = None,
    mirror_path: Path | None = None,
    worker: dict[str, Any] | None = None,
) -> dict:
    """Run evaluation checks on a worker's source repository.

    Parameters
    ----------
    worker_id:
        Worker database ID.
    source_repo:
        GitHub repository URL.
    storage:
        Connected AgentStorage instance.
    checks:
        Specific checks to run. Defaults to all.
    mirror_path:
        Optional path to an already-cloned mirror. If not provided, the
        repository will be cloned automatically for evaluation.

    Returns
    -------
    dict
        Evaluation report with per-check results and overall verdict.
    """
    from mas_core.worker_registry.ingestion import ingest_repository

    if mirror_path is None:
        logger.info("No mirror_path provided; ingesting repository for evaluation")
        mirror_path = await ingest_repository(str(worker_id), source_repo)

    check_funcs = {
        "provenance": _check_provenance,
        "version_pin": _check_version_pin,
        "manifest_validation": _check_manifest_validation,
        "architecture": _check_architecture,
        "maintenance": _check_maintenance,
        "licensing": _check_licensing,
        "security": _check_security,
        "trufflehog": _check_trufflehog,
        "semgrep": _check_semgrep,
        "compatibility": _check_compatibility,
        "sandbox_profile": _check_sandbox_profile,
        "budget_latency": _check_budget_latency,
        "approval": _check_approval_policy,
    }

    requested = checks or DEFAULT_GUARDED_CHECKS
    results: dict[str, dict] = {}

    for check_name in requested:
        func = check_funcs.get(check_name)
        if func is None:
            logger.warning("Unknown evaluation check: %s", check_name)
            continue

        try:
            if check_name in {
                "provenance",
                "version_pin",
                "sandbox_profile",
                "budget_latency",
                "approval",
            }:
                results[check_name] = await func(source_repo, mirror_path, worker or {})
            else:
                results[check_name] = await func(source_repo, mirror_path)
        except Exception as exc:
            logger.error("Check %s failed: %s", check_name, exc)
            results[check_name] = {
                "passed": False,
                "score": 0.0,
                "details": str(exc),
            }

    overall_score = _compute_overall_score(results)
    blocked_reasons = _blocked_reasons(results)
    risk_tier = _risk_tier(results, blocked_reasons)
    requires_human_approval = _requires_human_approval(results, risk_tier)
    verdict = _compute_verdict(results, overall_score, blocked_reasons, requires_human_approval)
    recommended_status = _recommended_status(verdict, requires_human_approval)

    report = await storage.create_evaluation_report(
        worker_id=worker_id,
        checks=results,
        overall_score=overall_score,
        verdict=verdict,
        evaluator_version=EVALUATOR_VERSION,
        risk_tier=risk_tier,
        blocked_reasons=blocked_reasons,
        recommended_status=recommended_status,
        requires_human_approval=requires_human_approval,
        report_id=uuid4(),
    )

    logger.info(
        "Evaluation for worker %s: verdict=%s, score=%.1f",
        worker_id,
        verdict,
        overall_score,
    )
    return report


async def _check_provenance(
    source_repo: str,
    mirror_path: Path | None,
    worker: dict[str, Any],
) -> dict:
    """Verify the repository source is explicit and traceable."""
    has_source = bool(source_repo)
    is_github = source_repo.startswith(("https://github.com/", "git@github.com:"))
    has_mirror = mirror_path is not None and mirror_path.exists()
    passed = has_source and has_mirror
    score = 100.0 if passed and is_github else 70.0 if passed else 0.0
    return {
        "passed": passed,
        "score": score,
        "details": f"source_repo set: {has_source}; github metadata: {is_github}; mirror present: {has_mirror}",
        "source_repo": source_repo,
        "source_revision": worker.get("source_revision") or worker.get("upstream_commit_sha"),
    }


async def _check_version_pin(
    source_repo: str,
    mirror_path: Path | None,
    worker: dict[str, Any],
) -> dict:
    """Require a tag, commit, branch, or recorded upstream revision for external workers."""
    pin = worker.get("version_pin") or worker.get("source_revision") or worker.get("upstream_commit_sha")
    passed = bool(pin)
    return {
        "passed": passed,
        "score": 100.0 if passed else 0.0,
        "details": f"version pin present: {passed}",
        "version_pin": pin,
    }


async def _check_manifest_validation(
    source_repo: str,
    mirror_path: Path | None,
) -> dict:
    """Validate the first recognized worker manifest in the repository."""
    if mirror_path is None:
        return {"passed": False, "score": 0.0, "details": "No mirror path provided"}

    candidates = [
        mirror_path / "aiat-worker.yaml",
        mirror_path / "aiat-worker.yml",
        mirror_path / "worker.yaml",
        mirror_path / "worker.yml",
    ]
    candidates.extend((mirror_path / "workers").glob("*.yaml") if (mirror_path / "workers").is_dir() else [])
    candidates.extend((mirror_path / "workers").glob("*.yml") if (mirror_path / "workers").is_dir() else [])

    for candidate in candidates:
        if not candidate.exists():
            continue
        try:
            manifest = WorkerManifest.model_validate(
                yaml.safe_load(candidate.read_text(encoding="utf-8")) or {}
            )
            return {
                "passed": True,
                "score": 100.0,
                "details": f"Validated manifest {candidate.relative_to(mirror_path)}",
                "manifest_id": manifest.metadata.id,
                "transport": manifest.runtime.transport,
                "sandbox_profile": manifest.sandbox.profile,
            }
        except Exception as exc:
            return {
                "passed": False,
                "score": 0.0,
                "details": f"Manifest {candidate.name} failed validation: {exc}",
            }

    return {"passed": False, "score": 0.0, "details": "No AIAT worker manifest found"}


async def _check_architecture(
    source_repo: str,
    mirror_path: Path | None,
) -> dict:
    """Check if the repo has a recognizable architecture and entrypoint."""
    if mirror_path is None:
        return {
            "passed": False,
            "score": 0.0,
            "details": "No mirror path provided; cannot inspect repository structure",
        }

    entrypoints_found = []
    for pattern in ENTRYPOINT_PATTERNS:
        for py_file in mirror_path.rglob("*.py"):
            if py_file.name.startswith("test_") or "_test.py" in py_file.name:
                continue
            try:
                content = py_file.read_text(encoding="utf-8", errors="ignore")
                if re.search(pattern, content):
                    entrypoints_found.append(f"{py_file.name}:{pattern}")
            except (OSError, UnicodeDecodeError):
                continue

    has_entrypoint = len(entrypoints_found) > 0
    has_structure = (
        (mirror_path / "pyproject.toml").exists()
        or (mirror_path / "setup.py").exists()
        or (mirror_path / "setup.cfg").exists()
        or (mirror_path / "requirements.txt").exists()
    )

    score = 0.0
    if has_entrypoint:
        score += 60.0
    if has_structure:
        score += 40.0

    return {
        "passed": score >= 50.0,
        "score": score,
        "details": f"Found {len(entrypoints_found)} potential entrypoint(s), "
        f"project structure: {has_structure}",
        "entrypoints": entrypoints_found[:5],
    }


async def _check_maintenance(
    source_repo: str,
    mirror_path: Path | None,
) -> dict:
    """Check maintenance quality via commit recency and CI presence."""
    import asyncio

    if mirror_path is None:
        return {
            "passed": False,
            "score": 0.0,
            "details": "No mirror path provided",
        }

    score = 0.0
    details = []

    try:
        proc = await asyncio.create_subprocess_exec(
            "git",
            "log",
            "--format=%ci",
            "-n",
            "10",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(mirror_path),
        )
        stdout, _ = await proc.communicate()
        if proc.returncode == 0:
            dates = stdout.decode().strip().split("\n")
            dates = [d.strip() for d in dates if d.strip()]
            if dates:
                details.append(f"Latest commit: {dates[0]}")
                score += 50.0
            if len(dates) >= 5:
                score += 25.0
                details.append(f"Has {len(dates)} recent commits")
    except Exception as exc:
        details.append(f"Could not read git log: {exc}")

    has_ci = (
        (mirror_path / ".github").exists()
        or (mirror_path / ".gitlab-ci.yml").exists()
        or (mirror_path / ".circleci").exists()
        or (mirror_path / "Jenkinsfile").exists()
    )
    if has_ci:
        score += 25.0
        details.append("CI/CD configuration found")

    has_docs = (
        (mirror_path / "README.md").exists()
        or (mirror_path / "README.rst").exists()
        or (mirror_path / "docs").is_dir()
    )
    if has_docs:
        details.append("Documentation present")

    return {
        "passed": score >= 50.0,
        "score": score,
        "details": "; ".join(details) if details else "No maintenance indicators found",
    }


async def _check_licensing(
    source_repo: str,
    mirror_path: Path | None,
) -> dict:
    """Check if the repository license is compatible."""
    if mirror_path is None:
        return {
            "passed": False,
            "score": 0.0,
            "details": "No mirror path provided",
        }

    license_files = [
        "LICENSE",
        "LICENSE.md",
        "LICENSE.txt",
        "COPYING",
        "LICENSE-MIT",
        "LICENSE-APACHE",
        "LICENSE-BSD",
    ]

    license_text = ""
    for lf in license_files:
        path = mirror_path / lf
        if path.exists():
            try:
                license_text = path.read_text(encoding="utf-8", errors="ignore").lower()
                break
            except OSError:
                continue

    if not license_text:
        return {
            "passed": False,
            "score": 0.0,
            "details": "No LICENSE file found",
        }

    detected = None
    for lic in COMPATIBLE_LICENSES:
        if lic in license_text:
            detected = lic
            break

    if detected is None:
        for lic in REJECTED_LICENSES:
            if lic in license_text:
                return {
                    "passed": False,
                    "score": 0.0,
                    "details": f"Rejected license detected: {lic}",
                    "license": lic,
                }

        return {
            "passed": False,
            "score": 20.0,
            "details": "License detected but not in recognized compatible list",
        }

    return {
        "passed": True,
        "score": 100.0,
        "details": f"Compatible license detected: {detected}",
        "license": detected,
    }


async def _check_security(
    source_repo: str,
    mirror_path: Path | None,
) -> dict:
    """Check for secrets, suspicious patterns, and dependency vulnerabilities."""
    if mirror_path is None:
        return {
            "passed": False,
            "score": 0.0,
            "details": "No mirror path provided",
        }

    secrets_found = []
    scanned_files = 0
    skipped_dirs = {".git", "node_modules", "__pycache__", ".venv", "venv", "dist", "build", ".tox"}

    for py_file in mirror_path.rglob("*.py"):
        if any(part in skipped_dirs for part in py_file.parts):
            continue
        if py_file.name.startswith("test_") or "_test.py" in py_file.name:
            continue
        try:
            content = py_file.read_text(encoding="utf-8", errors="ignore")
            scanned_files += 1
            for pattern in SECRET_PATTERNS:
                matches = re.findall(pattern, content)
                for match in matches:
                    secrets_found.append(f"{py_file.name}: potential secret")
        except (OSError, UnicodeDecodeError):
            continue

    has_env = (mirror_path / ".env").exists()
    has_env_example = (mirror_path / ".env.example").exists()

    score = 100.0
    if secrets_found:
        score -= 50.0
    if has_env and not has_env_example:
        score -= 20.0

    return {
        "passed": score >= 50.0,
        "score": score,
        "details": f"Scanned {scanned_files} files, "
        f"{len(secrets_found)} potential secret(s) found, "
        f".env present: {has_env}",
        "secrets_found": secrets_found[:10],
    }


def _tool_unavailable_result(tool_name: str) -> dict:
    return {
        "passed": True,
        "score": 100.0,
        "status": "SKIPPED_TOOL_UNAVAILABLE",
        "details": f"{tool_name} is not installed; optional executable check skipped",
    }


async def _check_trufflehog(source_repo: str, mirror_path: Path | None) -> dict:
    """Run TruffleHog when available; missing binary is a recorded skip."""
    if mirror_path is None:
        return {"passed": False, "score": 0.0, "details": "No mirror path provided"}

    binary = shutil.which("trufflehog")
    if binary is None:
        return _tool_unavailable_result("trufflehog")

    import asyncio

    proc = await asyncio.create_subprocess_exec(
        binary,
        "filesystem",
        "--json",
        str(mirror_path),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    findings = [line for line in stdout.decode(errors="ignore").splitlines() if line.strip()]
    passed = proc.returncode == 0 and not findings
    return {
        "passed": passed,
        "score": 100.0 if passed else 0.0,
        "status": "PASSED" if passed else "FAILED",
        "details": f"TruffleHog return code {proc.returncode}; findings: {len(findings)}",
        "findings_count": len(findings),
        "stderr": stderr.decode(errors="ignore")[:1000],
    }


async def _check_semgrep(source_repo: str, mirror_path: Path | None) -> dict:
    """Run Semgrep when available; missing binary is a recorded skip."""
    if mirror_path is None:
        return {"passed": False, "score": 0.0, "details": "No mirror path provided"}

    binary = shutil.which("semgrep")
    if binary is None:
        return _tool_unavailable_result("semgrep")

    import asyncio

    proc = await asyncio.create_subprocess_exec(
        binary,
        "scan",
        "--json",
        "--quiet",
        str(mirror_path),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    findings_count = 0
    try:
        payload = json.loads(stdout.decode(errors="ignore") or "{}")
        findings_count = len(payload.get("results") or [])
    except json.JSONDecodeError:
        findings_count = 0 if proc.returncode == 0 else 1
    passed = proc.returncode == 0 and findings_count == 0
    return {
        "passed": passed,
        "score": 100.0 if passed else 0.0,
        "status": "PASSED" if passed else "FAILED",
        "details": f"Semgrep return code {proc.returncode}; findings: {findings_count}",
        "findings_count": findings_count,
        "stderr": stderr.decode(errors="ignore")[:1000],
    }


async def _check_compatibility(
    source_repo: str,
    mirror_path: Path | None,
) -> dict:
    """Check if the repo can be adapted to our transport model."""
    if mirror_path is None:
        return {
            "passed": False,
            "score": 0.0,
            "details": "No mirror path provided",
        }

    score = 0.0
    details = []

    has_python = any(mirror_path.rglob("*.py"))
    if has_python:
        score += 30.0
        details.append("Python codebase detected")

    has_requirements = (mirror_path / "requirements.txt").exists() or (
        mirror_path / "pyproject.toml"
    ).exists()
    if has_requirements:
        score += 20.0
        details.append("Dependency management present")

    has_main = False
    for py_file in mirror_path.rglob("*.py"):
        try:
            content = py_file.read_text(encoding="utf-8", errors="ignore")
            if "__main__" in content or "def main(" in content:
                has_main = True
                break
        except (OSError, UnicodeDecodeError):
            continue

    if has_main:
        score += 30.0
        details.append("Runnable entrypoint detected")

    has_http = False
    for py_file in mirror_path.rglob("*.py"):
        try:
            content = py_file.read_text(encoding="utf-8", errors="ignore")
            if any(kw in content for kw in ["FastAPI", "Flask", "aiohttp", "http.server"]):
                has_http = True
                break
        except (OSError, UnicodeDecodeError):
            continue

    if has_http:
        score += 20.0
        details.append("HTTP server pattern detected (http transport viable)")

    return {
        "passed": score >= 50.0,
        "score": score,
        "details": "; ".join(details) if details else "No compatibility indicators found",
    }


async def _check_sandbox_profile(
    source_repo: str,
    mirror_path: Path | None,
    worker: dict[str, Any],
) -> dict:
    """Validate sandbox profile and minimal metadata shape."""
    profile = worker.get("sandbox_profile") or "restricted"
    if profile not in VALID_SANDBOX_PROFILES:
        return {
            "passed": False,
            "score": 0.0,
            "details": f"Invalid sandbox profile: {profile}",
            "valid_profiles": sorted(VALID_SANDBOX_PROFILES),
        }
    if _is_medium_or_dual_use_worker(worker) and profile not in HARDENED_SANDBOX_PROFILES:
        return {
            "passed": False,
            "score": 0.0,
            "details": "Medium/dual-use workers require gvisor or firecracker sandbox profile",
            "profile": profile,
            "required_profiles": sorted(HARDENED_SANDBOX_PROFILES),
        }
    score = 100.0 if profile in {"restricted", "gvisor", "firecracker"} else 70.0
    return {
        "passed": True,
        "score": score,
        "details": f"Sandbox profile '{profile}' is valid",
        "profile": profile,
        "filesystem": worker.get("sandbox_filesystem", {}),
        "network_mode": worker.get("sandbox_network_mode", "egress-allowlist"),
    }


async def _check_budget_latency(
    source_repo: str,
    mirror_path: Path | None,
    worker: dict[str, Any],
) -> dict:
    """Score whether the worker has bounded runtime metadata."""
    config = worker.get("adapter_config") or {}
    timeout = config.get("timeout_seconds") or config.get("timeout") or 300
    try:
        timeout_value = int(timeout)
    except (TypeError, ValueError):
        timeout_value = 300
    score = 100.0 if timeout_value <= 300 else 70.0 if timeout_value <= 900 else 40.0
    return {
        "passed": timeout_value <= 900,
        "score": score,
        "details": f"Adapter timeout budget: {timeout_value}s",
        "timeout_seconds": timeout_value,
    }


async def _check_approval_policy(
    source_repo: str,
    mirror_path: Path | None,
    worker: dict[str, Any],
) -> dict:
    """Evaluate policy posture before final verdict synthesis."""
    profile = worker.get("sandbox_profile") or "restricted"
    requires_approval = (
        profile in HARDENED_SANDBOX_PROFILES
        or bool(source_repo)
        or _is_medium_or_dual_use_worker(worker)
    )
    return {
        "passed": True,
        "score": 100.0,
        "details": "External workers require approval before activation"
        if requires_approval
        else "Internal worker does not require external adoption approval",
        "requires_human_approval": requires_approval,
    }


def _worker_risk_labels(worker: dict[str, Any]) -> set[str]:
    labels: set[str] = set()
    for key in ("risk_tier", "risk_level", "classification"):
        value = worker.get(key)
        if isinstance(value, str):
            labels.add(value.lower().replace("-", "_"))

    for nested_key in ("adapter_config", "wrapper_config"):
        nested = worker.get(nested_key)
        if not isinstance(nested, dict):
            continue
        for key in ("risk_tier", "risk_level", "classification"):
            value = nested.get(key)
            if isinstance(value, str):
                labels.add(value.lower().replace("-", "_"))
        if nested.get("dual_use") is True:
            labels.add("dual_use")

    tags = worker.get("tags")
    if isinstance(tags, list):
        labels.update(str(tag).lower().replace("-", "_") for tag in tags)
    return labels


def _is_medium_or_dual_use_worker(worker: dict[str, Any]) -> bool:
    labels = _worker_risk_labels(worker)
    return bool(labels & {"medium", "medium_risk", "dual_use", "dualuse"})


def _compute_overall_score(results: dict[str, dict]) -> float:
    """Compute weighted overall score from individual check results."""
    weights = {
        "provenance": 0.10,
        "version_pin": 0.10,
        "manifest_validation": 0.20,
        "trufflehog": 0.15,
        "semgrep": 0.15,
        "architecture": 0.20,
        "maintenance": 0.15,
        "licensing": 0.25,
        "security": 0.25,
        "compatibility": 0.15,
        "sandbox_profile": 0.10,
        "budget_latency": 0.05,
        "approval": 0.00,
    }

    total = 0.0
    total_weight = 0.0
    for check_name, result in results.items():
        weight = weights.get(check_name, 0.1)
        score = result.get("score", 0.0)
        total += weight * score
        total_weight += weight

    if total_weight == 0:
        return 0.0

    return round(total / total_weight, 1)


def _blocked_reasons(results: dict[str, dict]) -> list[str]:
    """Return human-readable blockers for failed non-optional checks."""
    blockers: list[str] = []
    for check_name, result in results.items():
        if result.get("passed", False):
            continue
        details = result.get("details") or "check failed"
        blockers.append(f"{check_name}: {details}")
    return blockers


def _risk_tier(results: dict[str, dict], blocked_reasons: list[str]) -> str:
    """Classify adoption risk from failed checks and skipped executable scans."""
    high_checks = {"licensing", "security", "trufflehog", "semgrep", "manifest_validation"}
    if any(name in reason for name in high_checks for reason in blocked_reasons):
        return "high"
    if blocked_reasons:
        return "medium"
    skipped_optional = any(
        result.get("status") == "SKIPPED_TOOL_UNAVAILABLE"
        for name, result in results.items()
        if name in {"trufflehog", "semgrep"}
    )
    if skipped_optional:
        return "medium"
    return "low"


def _requires_human_approval(results: dict[str, dict], risk_tier: str) -> bool:
    approval_check = results.get("approval", {})
    return bool(approval_check.get("requires_human_approval")) or risk_tier != "low"


def _compute_verdict(
    results: dict[str, dict],
    overall_score: float,
    blocked_reasons: list[str] | None = None,
    requires_human_approval: bool = False,
) -> str:
    """Determine the overall verdict from check results and score."""
    blocked_reasons = blocked_reasons or []
    licensing = results.get("licensing")
    if licensing is not None and licensing.get("score", 0) < 50:
        return "REJECTED"

    security = results.get("security")
    if security is not None and security.get("score", 0) < 30:
        return "REJECTED"

    if blocked_reasons:
        return "REJECTED"

    if requires_human_approval:
        return "CONDITIONAL"

    if overall_score >= 70:
        return "APPROVED"
    if overall_score >= 50:
        return "CONDITIONAL"
    return "REJECTED"


def _recommended_status(verdict: str, requires_human_approval: bool) -> str:
    if verdict == "REJECTED":
        return "REJECTED"
    if requires_human_approval:
        return "PENDING_APPROVAL"
    if verdict == "APPROVED":
        return "ACTIVE"
    return "PENDING_EVALUATION"

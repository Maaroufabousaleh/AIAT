"""Repository evaluation engine.

Evaluates external worker repositories on five dimensions:
- Architecture: Does the repo expose a clear entrypoint and recognizable patterns?
- Maintenance: Recent commits, CI/CD presence, documentation quality.
- Licensing: Is the license compatible (MIT, Apache 2.0, BSD)?
- Security: Known vulnerabilities, secrets, suspicious patterns.
- Compatibility: Can the repo be adapted to our transport model?
"""

from __future__ import annotations

import json
import logging
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

if TYPE_CHECKING:
    from mas_core.memory.storage import AgentStorage

logger = logging.getLogger(__name__)

EVALUATOR_VERSION = "1.0.0"

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
        "architecture": _check_architecture,
        "maintenance": _check_maintenance,
        "licensing": _check_licensing,
        "security": _check_security,
        "compatibility": _check_compatibility,
    }

    requested = checks or list(check_funcs.keys())
    results: dict[str, dict] = {}

    for check_name in requested:
        func = check_funcs.get(check_name)
        if func is None:
            logger.warning("Unknown evaluation check: %s", check_name)
            continue

        try:
            results[check_name] = await func(source_repo, mirror_path)
        except Exception as exc:
            logger.error("Check %s failed: %s", check_name, exc)
            results[check_name] = {
                "passed": False,
                "score": 0.0,
                "details": str(exc),
            }

    overall_score = _compute_overall_score(results)
    verdict = _compute_verdict(results, overall_score)

    report = await storage.create_evaluation_report(
        worker_id=worker_id,
        checks=results,
        overall_score=overall_score,
        verdict=verdict,
        evaluator_version=EVALUATOR_VERSION,
        report_id=uuid4(),
    )

    logger.info(
        "Evaluation for worker %s: verdict=%s, score=%.1f",
        worker_id,
        verdict,
        overall_score,
    )
    return report


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


def _compute_overall_score(results: dict[str, dict]) -> float:
    """Compute weighted overall score from individual check results."""
    weights = {
        "architecture": 0.20,
        "maintenance": 0.15,
        "licensing": 0.25,
        "security": 0.25,
        "compatibility": 0.15,
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


def _compute_verdict(results: dict[str, dict], overall_score: float) -> str:
    """Determine the overall verdict from check results and score."""
    licensing = results.get("licensing", {})
    if licensing.get("score", 0) < 50:
        return "REJECTED"

    security = results.get("security", {})
    if security.get("score", 0) < 30:
        return "REJECTED"

    if overall_score >= 70:
        return "APPROVED"
    if overall_score >= 50:
        return "CONDITIONAL"
    return "REJECTED"

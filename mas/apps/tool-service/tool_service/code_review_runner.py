"""Deterministic, credential-free structured Git diff reviewer."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

_RULES = (
    ("hardcoded-secret", "high", re.compile(r"(?i)(api[_-]?key|password|secret|token)\s*[:=]\s*['\"][^'\"]+"), "Potential hardcoded credential."),
    ("shell-execution", "high", re.compile(r"\b(shell\s*=\s*True|os\.system\s*\(|eval\s*\(|exec\s*\()"), "Unsafe dynamic or shell execution."),
    ("tls-disabled", "high", re.compile(r"\b(verify\s*=\s*False|NODE_TLS_REJECT_UNAUTHORIZED\s*=\s*['\"]?0)"), "TLS verification is disabled."),
    ("broad-exception", "medium", re.compile(r"^\s*except\s+(Exception|BaseException)\s*:\s*$"), "Broad exception handler can hide actionable failures."),
    ("debug-artifact", "low", re.compile(r"\b(print\s*\(|console\.log\s*\()"), "Debug output was added."),
)
_LEVELS = {"low": 1, "medium": 2, "high": 3, "critical": 4}


def _git_diff(cwd: Path, payload: dict[str, Any]) -> str:
    mode = str(payload.get("mode") or "diff")
    if mode != "diff":
        raise ValueError("only diff mode is supported")
    base = str(payload.get("base") or "").strip()
    head = str(payload.get("head") or "HEAD").strip()
    if base:
        revision = f"{base}...{head}"
        command = ["git", "diff", "--no-ext-diff", "--unified=0", revision]
    else:
        command = ["git", "diff", "--no-ext-diff", "--unified=0", head]
    with tempfile.TemporaryFile() as stdout_file, tempfile.TemporaryFile() as stderr_file:
        process = subprocess.Popen(command, cwd=cwd, stdout=stdout_file, stderr=stderr_file)
        try:
            returncode = process.wait(timeout=60)
        except subprocess.TimeoutExpired as exc:
            process.kill()
            process.wait()
            raise RuntimeError("git diff timed out") from exc
        stdout_size = stdout_file.tell()
        if stdout_size > 2_000_000:
            raise RuntimeError("git diff exceeds the 2 MB review limit")
        stdout_file.seek(0)
        stderr_file.seek(0)
        stdout = stdout_file.read().decode("utf-8", errors="replace")
        stderr = stderr_file.read(64_000).decode("utf-8", errors="replace")
    if returncode != 0:
        raise RuntimeError(stderr.strip() or "git diff failed")
    return stdout


def review(cwd: Path, payload: dict[str, Any]) -> dict[str, Any]:
    threshold = str(payload.get("severity_threshold") or "medium").lower()
    if threshold not in _LEVELS:
        raise ValueError("severity_threshold must be low, medium, high, or critical")
    diff = _git_diff(cwd, payload)
    findings: list[dict[str, Any]] = []
    current_file = ""
    new_line = 0
    for line in diff.splitlines():
        if line.startswith("+++ b/"):
            current_file = line[6:]
            continue
        if line.startswith("@@"):
            match = re.search(r"\+(\d+)", line)
            new_line = int(match.group(1)) if match else 0
            continue
        if not line.startswith("+") or line.startswith("+++"):
            continue
        added = line[1:]
        for rule_id, severity, pattern, message in _RULES:
            if _LEVELS[severity] < _LEVELS[threshold] or not pattern.search(added):
                continue
            findings.append(
                {
                    "rule_id": rule_id,
                    "severity": severity,
                    "path": current_file,
                    "line": new_line,
                    "message": message,
                }
            )
        new_line += 1
    return {
        "available": True,
        "backend": "aiat_deterministic_diff_review",
        "mode": "diff",
        "severity_threshold": threshold,
        "files_reviewed": len({item[6:] for item in diff.splitlines() if item.startswith("+++ b/")}),
        "findings": findings,
        "findings_count": len(findings),
        "passed": not findings,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--json-stdin", action="store_true", required=True)
    parser.parse_args()
    try:
        payload = json.load(sys.stdin)
        if not isinstance(payload, dict):
            raise ValueError("review request must be a JSON object")
        result = review(Path.cwd(), payload)
    except Exception as exc:
        result = {"available": False, "error": str(exc)}
    json.dump(result, sys.stdout)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()

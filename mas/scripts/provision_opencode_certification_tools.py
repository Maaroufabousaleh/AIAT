"""Provision the exact tools used by the OpenCode candidate certification.

The workflow runs this command in an ephemeral runner before scanning.  Each
tool is pinned to an explicit package/release source, downloaded artifacts are
hash checked, and installation stdout/stderr is retained in the evidence
directory.  A failed installation is a tooling blocker; it is never reported
as a clean scan.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.error
import urllib.request
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable

SCHEMA = "aiat.opencode-tool-provisioning.v1"
TOOL_INSTALLATION_FAILURE = "TOOL_INSTALLATION_FAILURE"

TOOL_SPECS: tuple[dict[str, Any], ...] = (
    {
        "name": "semgrep",
        "kind": "python-package",
        "requirement": "semgrep==1.168.0",
        "source": "https://pypi.org/project/semgrep/1.168.0/",
        "executable": "semgrep",
        "version_command": ["--version"],
    },
    {
        "name": "skillspector",
        "kind": "git-package",
        "requirement": "skillspector @ git+https://github.com/NVIDIA/skillspector@698e2bf29c7d32aa8211ada677382460c01900d7",
        "source": "https://github.com/NVIDIA/skillspector/tree/698e2bf29c7d32aa8211ada677382460c01900d7",
        "executable": "skillspector",
        "version_command": ["--version"],
    },
    {
        "name": "trufflehog",
        "kind": "release-archive",
        "version": "3.97.0",
        "source": "https://github.com/trufflesecurity/trufflehog/releases/download/v3.97.0/trufflehog_3.97.0_linux_amd64.tar.gz",
        "sha256": "62224de2f9dd7cd418800feb953760a302ed2f82a7c547fe1146a4874fb179e4",
        "executable": "trufflehog",
        "version_command": ["--version"],
    },
    {
        "name": "syft",
        "kind": "release-archive",
        "version": "1.51.0",
        "source": "https://github.com/anchore/syft/releases/download/v1.51.0/syft_1.51.0_linux_amd64.tar.gz",
        "sha256": "2a2e837a2c8d59ec9af5472ee22d3b04ee463c4e44476ecf993fd1e5ab6ebc7f",
        "executable": "syft",
        "version_command": ["version"],
    },
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _run(command: list[str], *, timeout: float = 900.0, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, capture_output=True, text=True, timeout=timeout, check=False, env=env)


def _redact(text: str) -> str:
    # Tool installation should not receive credentials, but keep a minimal
    # defence for accidental bearer/token-shaped output in package logs.
    import re

    return re.sub(r"(?i)(bearer\s+|gh[pousr]_)[a-z0-9._~+/=-]{12,}", "[REDACTED]", text)


def _write_log(output_dir: Path, name: str, result: subprocess.CompletedProcess[str]) -> tuple[str, str]:
    stdout_name = f"{name}.install.stdout.log"
    stderr_name = f"{name}.install.stderr.log"
    (output_dir / stdout_name).write_text(_redact(result.stdout or ""), encoding="utf-8")
    (output_dir / stderr_name).write_text(_redact(result.stderr or ""), encoding="utf-8")
    return stdout_name, stderr_name


def _safe_extract(archive: Path, target: Path) -> None:
    target_resolved = target.resolve()
    with tarfile.open(archive, mode="r:gz") as handle:
        members = handle.getmembers()
        for member in members:
            destination = (target / member.name).resolve()
            if destination != target_resolved and target_resolved not in destination.parents:
                raise RuntimeError("release archive contains an unsafe path")
        handle.extractall(target)


def _download(url: str, destination: Path) -> None:
    request = urllib.request.Request(url, headers={"User-Agent": "AIAT-opencode-certification"})
    with urllib.request.urlopen(request, timeout=180) as response, destination.open("wb") as handle:
        shutil.copyfileobj(response, handle)


def _version(executable: Path, args: list[str], *, runner: Callable[..., subprocess.CompletedProcess[str]]) -> tuple[str | None, subprocess.CompletedProcess[str]]:
    result = runner([str(executable), *args], timeout=60)
    text = (result.stdout or result.stderr or "").strip()
    return text.splitlines()[0] if text else None, result


def _python_install(
    spec: dict[str, Any],
    *,
    venv_python: Path,
    venv_bin: Path,
    output_dir: Path,
    runner: Callable[..., subprocess.CompletedProcess[str]],
) -> dict[str, Any]:
    command = [
        str(venv_python),
        "-m",
        "pip",
        "install",
        "--disable-pip-version-check",
        "--no-input",
        "--no-cache-dir",
        "--require-virtualenv",
        str(spec["requirement"]),
    ]
    try:
        result = runner(command, timeout=1800)
    except (OSError, subprocess.SubprocessError) as exc:
        return {
            "name": spec["name"],
            "kind": spec["kind"],
            "source": spec["source"],
            "requirement": spec["requirement"],
            "status": "failed",
            "failure_class": TOOL_INSTALLATION_FAILURE,
            "error_type": type(exc).__name__,
        }
    stdout_name, stderr_name = _write_log(output_dir, spec["name"], result)
    executable = venv_bin / spec["executable"]
    row: dict[str, Any] = {
        "name": spec["name"],
        "kind": spec["kind"],
        "source": spec["source"],
        "requirement": spec["requirement"],
        "status": "pass" if result.returncode == 0 and executable.exists() else "failed",
        "exit_status": result.returncode,
        "stdout_path": stdout_name,
        "stderr_path": stderr_name,
        "executable": str(executable),
    }
    if row["status"] != "pass":
        row["failure_class"] = TOOL_INSTALLATION_FAILURE
        row["error_type"] = "install_failed_or_executable_missing"
        return row
    version, version_result = _version(executable, spec["version_command"], runner=runner)
    row["version"] = version
    row["version_exit_status"] = version_result.returncode
    if version_result.returncode != 0 or not version:
        row["status"] = "failed"
        row["failure_class"] = TOOL_INSTALLATION_FAILURE
        row["error_type"] = "version_probe_failed"
    return row


def _archive_install(
    spec: dict[str, Any],
    *,
    bin_dir: Path,
    output_dir: Path,
    runner: Callable[..., subprocess.CompletedProcess[str]],
    downloader: Callable[[str, Path], None],
) -> dict[str, Any]:
    row: dict[str, Any] = {
        "name": spec["name"],
        "kind": spec["kind"],
        "version": spec["version"],
        "source": spec["source"],
        "expected_sha256": spec["sha256"],
    }
    archive: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(prefix=f"aiat-{spec['name']}-", suffix=".tar.gz", delete=False) as handle:
            archive = Path(handle.name)
        downloader(spec["source"], archive)
        actual_sha256 = _sha256(archive)
        row["actual_sha256"] = actual_sha256
        if actual_sha256 != spec["sha256"]:
            raise RuntimeError("download checksum mismatch")
        bin_dir.mkdir(parents=True, exist_ok=True)
        _safe_extract(archive, bin_dir)
        executable = bin_dir / spec["executable"]
        if not executable.is_file():
            raise RuntimeError("release executable missing")
        executable.chmod(executable.stat().st_mode | 0o111)
        version, version_result = _version(executable, spec["version_command"], runner=runner)
        row.update(
            {
                "status": "pass" if version_result.returncode == 0 and version else "failed",
                "executable": str(executable),
                "version_probe_exit_status": version_result.returncode,
                "reported_version": version,
            }
        )
        if row["status"] != "pass":
            row.update({"failure_class": TOOL_INSTALLATION_FAILURE, "error_type": "version_probe_failed"})
    except (OSError, RuntimeError, tarfile.TarError, urllib.error.URLError) as exc:
        row.update({"status": "failed", "failure_class": TOOL_INSTALLATION_FAILURE, "error_type": type(exc).__name__})
    finally:
        if archive is not None:
            archive.unlink(missing_ok=True)
    return row


def provision_tools(
    *,
    output_dir: Path,
    install_dir: Path,
    python_executable: str = sys.executable,
    runner: Callable[..., subprocess.CompletedProcess[str]] = _run,
    downloader: Callable[[str, Path], None] = _download,
) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    install_dir.mkdir(parents=True, exist_ok=True)
    venv_dir = install_dir / "venv"
    venv_bin = venv_dir / ("Scripts" if os.name == "nt" else "bin")
    rows: list[dict[str, Any]] = []
    venv_result: subprocess.CompletedProcess[str] | None = None
    try:
        venv_result = runner([python_executable, "-m", "venv", str(venv_dir)], timeout=300)
    except (OSError, subprocess.SubprocessError) as exc:
        venv_result = subprocess.CompletedProcess([], 1, "", type(exc).__name__)
    if venv_result.returncode == 0:
        venv_python = venv_bin / ("python.exe" if os.name == "nt" else "python")
        for spec in TOOL_SPECS[:2]:
            rows.append(_python_install(spec, venv_python=venv_python, venv_bin=venv_bin, output_dir=output_dir, runner=runner))
    else:
        for spec in TOOL_SPECS[:2]:
            rows.append(
                {
                    "name": spec["name"],
                    "kind": spec["kind"],
                    "source": spec["source"],
                    "requirement": spec["requirement"],
                    "status": "failed",
                    "failure_class": TOOL_INSTALLATION_FAILURE,
                    "error_type": "python_venv_creation_failed",
                }
            )
    bin_dir = install_dir / "bin"
    for spec in TOOL_SPECS[2:]:
        rows.append(_archive_install(spec, bin_dir=bin_dir, output_dir=output_dir, runner=runner, downloader=downloader))
    path_entries = [str(venv_bin), str(bin_dir)]
    (output_dir / "tooling-paths.txt").write_text("\n".join(path_entries) + "\n", encoding="utf-8")
    failures = [row for row in rows if row.get("status") != "pass"]
    report = {
        "schema_version": SCHEMA,
        "status": "pass" if not failures else "blocked",
        "required_tools": [row["name"] for row in TOOL_SPECS],
        "tools": rows,
        "failure_classes": sorted({row["failure_class"] for row in failures if row.get("failure_class")} ),
        "installation_errors": [
            {"tool": row["name"], "error_type": row.get("error_type"), "failure_class": row.get("failure_class")}
            for row in failures
        ],
        "path_entries": path_entries,
        "credentials_persisted": False,
        "payloads_persisted": False,
        "licence_metadata_is_gate": False,
    }
    (output_dir / "tooling-provisioning.json").write_text(json.dumps(report, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--install-dir", type=Path, required=True)
    parser.add_argument("--python", default=sys.executable)
    args = parser.parse_args(argv)
    report = provision_tools(output_dir=args.output, install_dir=args.install_dir, python_executable=args.python)
    print(json.dumps({"status": report["status"], "required_tools": report["required_tools"], "failure_classes": report["failure_classes"]}, sort_keys=True, indent=2))
    return 0 if report["status"] == "pass" else 2


if __name__ == "__main__":
    raise SystemExit(main())

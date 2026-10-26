"""Server-side static scan for the pinned first-party deterministic worker.

Only the fixed worker source and Dockerfile are copied into a private temporary
directory and read by fixed, digest/version-pinned local scanners. A separately
content-pinned BuildKit record binds that source and recipe to the OCI image.
No source checkout, worker process, shell, or caller-provided path is involved.
Scanner invocations disable their expected telemetry/update paths, but the
subprocesses inherit the orchestrator's network namespace; filtering
environment variables does not enforce network isolation. Raw scanner output
is kept only in bounded process memory and is never logged or persisted;
stored findings contain identifiers and locations only.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import pwd
import re
import shutil
import signal
import stat
import tempfile
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from mas_core.worker_registry.source_bound_security_scan import (
    FIRST_PARTY_BUILD_PROVENANCE_PATH,
    FIRST_PARTY_BUILD_PROVENANCE_SHA256,
    FIRST_PARTY_BUILD_PROVENANCE_SUMMARY,
    FIRST_PARTY_DOCKERFILE_SHA256,
    FIRST_PARTY_IMAGE_DIGEST,
    FIRST_PARTY_MANIFEST_SHA256,
    FIRST_PARTY_RUNTIME_SHA256,
    FIRST_PARTY_SEMGREP_CONFIG_SHA256,
    FIRST_PARTY_SOURCE_REPOSITORY,
    FIRST_PARTY_SOURCE_REVISION,
    FIRST_PARTY_WORKER_ID,
    FIRST_PARTY_WORKER_VERSION,
    PINNED_SCANNERS,
    SOURCE_BOUND_SCAN_SCHEMA,
    finalize_source_bound_scan_report,
    first_party_build_provenance_summary_is_valid,
)

MAX_INPUT_FILE_BYTES = 1024 * 1024
MAX_SCANNER_TIMEOUT_SECONDS = 120.0
MAX_FINDINGS_PER_SCANNER = 256
_IMAGE_REF = f"aiat-deterministic-file-worker@{FIRST_PARTY_IMAGE_DIGEST}"
_VERSION_PIN = f"{FIRST_PARTY_WORKER_VERSION}+{FIRST_PARTY_IMAGE_DIGEST.replace(':', '.')}"
_SOURCE_RELATIVE_PATH = "mas/workers/aiat_deterministic_file_worker_process.py"
_DOCKERFILE_RELATIVE_PATH = "mas/workers/Dockerfile.aiat_deterministic_file_worker"
_SOURCE_FILENAME = "aiat_deterministic_file_worker_process.py"
_DOCKERFILE_FILENAME = "Dockerfile.aiat_deterministic_file_worker"
_MANIFEST_FILENAME = f"{FIRST_PARTY_WORKER_ID}.yaml"
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class WorkerSecurityScanError(ValueError):
    """A bounded, safe reason the source-bound scan could not be prepared."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(64 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _repo_root_and_inputs() -> tuple[Path, dict[str, Path], Path, Path]:
    """Find the image-bundled or checked-out files without caller path input."""
    roots = (Path.cwd().resolve(), *Path(__file__).resolve().parents)
    seen: set[Path] = set()
    for root in roots:
        if root in seen:
            continue
        seen.add(root)
        workers = root / "workers"
        manifest = workers / _MANIFEST_FILENAME
        source = workers / _SOURCE_FILENAME
        dockerfile = workers / _DOCKERFILE_FILENAME
        if (manifest.is_file() and source.is_file() and dockerfile.is_file()):
            local_config = root / "apps" / "tool-service" / "tool_service" / "semgrep-default.yml"
            image_config = root / "semgrep-default.yml"
            config = local_config if local_config.is_file() else image_config
            if config.is_file():
                return root, {"manifest": manifest, "source": source, "dockerfile": dockerfile}, config, root
    raise WorkerSecurityScanError("candidate_scan_inputs_unavailable")


def _read_regular_file(path: Path) -> bytes:
    try:
        info = path.lstat()
    except OSError as exc:
        raise WorkerSecurityScanError("candidate_scan_input_unavailable") from exc
    if not stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode):
        raise WorkerSecurityScanError("candidate_scan_input_not_regular")
    if info.st_size > MAX_INPUT_FILE_BYTES:
        raise WorkerSecurityScanError("candidate_scan_input_too_large")
    try:
        return path.read_bytes()
    except OSError as exc:
        raise WorkerSecurityScanError("candidate_scan_input_unavailable") from exc


def _mapping(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _validate_manifest_and_registered_identity(
    *,
    manifest_bytes: bytes,
    source_bytes: bytes,
    dockerfile_bytes: bytes,
    config_bytes: bytes,
    worker: dict[str, Any],
    provenance: dict[str, Any],
) -> dict[str, Any]:
    try:
        manifest = _mapping(yaml.safe_load(manifest_bytes))
    except yaml.YAMLError as exc:
        raise WorkerSecurityScanError("candidate_manifest_invalid") from exc
    metadata = _mapping(manifest.get("metadata"))
    runtime = _mapping(manifest.get("runtime"))
    runtime_config = _mapping(runtime.get("adapter_config"))
    source_provenance = _mapping(manifest.get("source_provenance"))
    integration = _mapping(manifest.get("integration"))
    execution = _mapping(manifest.get("execution"))
    sandbox = _mapping(manifest.get("sandbox"))
    expected_runtime_sha = _sha256_bytes(source_bytes)
    expected_dockerfile_sha = _sha256_bytes(dockerfile_bytes)
    expected_config_sha = _sha256_bytes(config_bytes)

    manifest_valid = (
        _sha256_bytes(manifest_bytes) == FIRST_PARTY_MANIFEST_SHA256
        and metadata.get("id") == FIRST_PARTY_WORKER_ID
        and str(metadata.get("version")) == FIRST_PARTY_WORKER_VERSION
        and metadata.get("source_repo") == FIRST_PARTY_SOURCE_REPOSITORY
        and metadata.get("source_revision") == FIRST_PARTY_SOURCE_REVISION
        and metadata.get("version_pin") == _VERSION_PIN
        and runtime.get("transport") == "oci"
        and runtime_config.get("image") == _IMAGE_REF
        and integration.get("adapter_entrypoint") == "ProcessAdapter"
        and execution.get("trust") == "untrusted"
        and execution.get("minimum_isolation") == "sandboxed"
        and sandbox.get("profile") == "sandboxed"
        and manifest.get("model_mode") == "none"
        and manifest.get("tool_grants") == ["file_write"]
        and source_provenance.get("source_provider") == "aiat"
        and source_provenance.get("commit_sha") == FIRST_PARTY_SOURCE_REVISION
        and source_provenance.get("oci_image_digest") == FIRST_PARTY_IMAGE_DIGEST
        and source_provenance.get("runtime_fingerprint") == f"sha256:{FIRST_PARTY_RUNTIME_SHA256}"
        and expected_runtime_sha == FIRST_PARTY_RUNTIME_SHA256
        and expected_dockerfile_sha == FIRST_PARTY_DOCKERFILE_SHA256
        and expected_config_sha == FIRST_PARTY_SEMGREP_CONFIG_SHA256
    )
    if not manifest_valid:
        raise WorkerSecurityScanError("candidate_source_identity_mismatch")

    worker_config = _mapping(worker.get("adapter_config"))
    worker_valid = (
        str(worker.get("name") or "") == FIRST_PARTY_WORKER_ID
        and str(worker.get("version") or "") == FIRST_PARTY_WORKER_VERSION
        and str(worker.get("source_repo") or "") == FIRST_PARTY_SOURCE_REPOSITORY
        and str(worker.get("source_revision") or "") == FIRST_PARTY_SOURCE_REVISION
        and str(worker.get("version_pin") or "") == _VERSION_PIN
        and str(worker.get("adapter_type") or "").lower() == "oci"
        and str(worker.get("sandbox_profile") or "") == "sandboxed"
        and str(worker.get("adapter_entrypoint") or "") == "ProcessAdapter"
        and worker_config.get("image") == _IMAGE_REF
    )
    provenance_valid = (
        str(provenance.get("canonical_source_repository") or "") == FIRST_PARTY_SOURCE_REPOSITORY
        and str(provenance.get("source_provider") or "").lower() == "aiat"
        and str(provenance.get("exact_release") or "") == FIRST_PARTY_WORKER_VERSION
        and str(provenance.get("commit_sha") or "") == FIRST_PARTY_SOURCE_REVISION
        and str(provenance.get("oci_image_digest") or "") == FIRST_PARTY_IMAGE_DIGEST
        and str(provenance.get("transport_type") or "").lower() == "oci"
        and str(provenance.get("runtime_fingerprint") or "") == f"sha256:{FIRST_PARTY_RUNTIME_SHA256}"
    )
    if not worker_valid or not provenance_valid:
        raise WorkerSecurityScanError("registered_candidate_identity_mismatch")

    return {
        "manifest_sha256": _sha256_bytes(manifest_bytes),
        "runtime_sha256": expected_runtime_sha,
        "dockerfile_sha256": expected_dockerfile_sha,
        "semgrep_config_sha256": expected_config_sha,
    }


def _load_first_party_image_build_provenance(root: Path) -> dict[str, Any]:
    """Read the one reviewed build record and bind its exact facts to the scan.

    The evidence file hash is pinned in the candidate validator. This check
    does not claim an authenticated builder: the retained local BuildKit record
    is explicitly unsigned and remains subject to human candidate approval.
    """
    evidence_path = root / FIRST_PARTY_BUILD_PROVENANCE_PATH
    evidence_bytes = _read_regular_file(evidence_path)
    if _sha256_bytes(evidence_bytes) != FIRST_PARTY_BUILD_PROVENANCE_SHA256:
        raise WorkerSecurityScanError("candidate_build_provenance_mismatch")
    try:
        evidence = json.loads(evidence_bytes)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise WorkerSecurityScanError("candidate_build_provenance_invalid") from exc
    if not isinstance(evidence, dict):
        raise WorkerSecurityScanError("candidate_build_provenance_invalid")

    candidate = _mapping(evidence.get("candidate"))
    source = _mapping(evidence.get("source"))
    recipe = _mapping(evidence.get("recipe"))
    builder = _mapping(evidence.get("builder"))
    build = _mapping(evidence.get("build"))
    image = _mapping(evidence.get("image"))
    attestation = _mapping(evidence.get("attestation"))
    qualification = _mapping(evidence.get("qualification"))
    statement = _mapping(attestation.get("statement"))
    facts_valid = (
        evidence.get("schema_version") == "aiat.deterministic-worker.build-provenance.v1"
        and candidate == {"worker_id": FIRST_PARTY_WORKER_ID, "version": FIRST_PARTY_WORKER_VERSION}
        and source.get("repository") == FIRST_PARTY_SOURCE_REPOSITORY
        and source.get("revision") == FIRST_PARTY_BUILD_PROVENANCE_SUMMARY["source_revision"]
        and source.get("sha256") == FIRST_PARTY_BUILD_PROVENANCE_SUMMARY["source_sha256"]
        and source.get("context_sha256") == FIRST_PARTY_BUILD_PROVENANCE_SUMMARY["context_sha256"]
        and source.get("context_entries") == ["aiat_deterministic_file_worker_process.py"]
        and recipe.get("path") == "mas/workers/Dockerfile.aiat_deterministic_file_worker"
        and recipe.get("sha256") == FIRST_PARTY_BUILD_PROVENANCE_SUMMARY["recipe_sha256"]
        and builder.get("builder_name") == FIRST_PARTY_BUILD_PROVENANCE_SUMMARY["builder_name"]
        and builder.get("authenticated") == "false"
        and builder.get("trust") == "unauthenticated-local-builder"
        and build.get("source_revision") == FIRST_PARTY_BUILD_PROVENANCE_SUMMARY["source_revision"]
        and build.get("builder_id") == ""
        and build.get("source_sha256") == FIRST_PARTY_BUILD_PROVENANCE_SUMMARY["source_sha256"]
        and build.get("context_sha256") == FIRST_PARTY_BUILD_PROVENANCE_SUMMARY["context_sha256"]
        and build.get("recipe_sha256") == FIRST_PARTY_BUILD_PROVENANCE_SUMMARY["recipe_sha256"]
        and build.get("base_image") == FIRST_PARTY_BUILD_PROVENANCE_SUMMARY["base_image"]
        and build.get("base_digest") == FIRST_PARTY_BUILD_PROVENANCE_SUMMARY["base_digest"]
        and build.get("oci_index_digest") == FIRST_PARTY_IMAGE_DIGEST
        and build.get("platform_manifest_digest")
        == FIRST_PARTY_BUILD_PROVENANCE_SUMMARY["platform_manifest_digest"]
        and build.get("attestation_subject_digest")
        == FIRST_PARTY_BUILD_PROVENANCE_SUMMARY["platform_manifest_digest"]
        and image.get("oci_index_digest") == FIRST_PARTY_IMAGE_DIGEST
        and image.get("runtime_reference") == _IMAGE_REF
        and image.get("platform") == "linux/amd64"
        and image.get("platform_manifest_digest")
        == FIRST_PARTY_BUILD_PROVENANCE_SUMMARY["platform_manifest_digest"]
        and attestation.get("manifest_digest")
        == FIRST_PARTY_BUILD_PROVENANCE_SUMMARY["attestation_manifest_digest"]
        and attestation.get("subject_digest")
        == FIRST_PARTY_BUILD_PROVENANCE_SUMMARY["attestation_subject_digest"]
        and attestation.get("predicate_type")
        == FIRST_PARTY_BUILD_PROVENANCE_SUMMARY["predicate_type"]
        and build.get("statement_sha256")
        == FIRST_PARTY_BUILD_PROVENANCE_SUMMARY["statement_sha256"]
        and statement.get("_type") == "https://in-toto.io/Statement/v1"
        and statement.get("predicateType")
        == FIRST_PARTY_BUILD_PROVENANCE_SUMMARY["predicate_type"]
        and qualification.get("status") == "local_build_binding_observed"
        and qualification.get("signed") is False
        and qualification.get("builder_authenticated") is False
        and qualification.get("trusted_builder_claimed") is False
        and qualification.get("admission_granted") is False
    )
    summary = dict(FIRST_PARTY_BUILD_PROVENANCE_SUMMARY)
    if not facts_valid or not first_party_build_provenance_summary_is_valid(summary):
        raise WorkerSecurityScanError("candidate_build_provenance_mismatch")
    return summary


async def _capture_bounded(reader: asyncio.StreamReader, limit: int) -> bytes:
    output = bytearray()
    while True:
        chunk = await reader.read(64 * 1024)
        if not chunk:
            return bytes(output)
        if len(output) + len(chunk) > limit:
            raise WorkerSecurityScanError("scanner_output_limit_exceeded")
        output.extend(chunk)


async def _kill_process_group(process: asyncio.subprocess.Process) -> None:
    """Kill the isolated scanner process group, including surviving children.

    The group can outlive its leader when a child inherits stdout or stderr, so
    checking ``returncode`` before signaling would leave that child running.
    """
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    except PermissionError as exc:
        # Best effort for the direct child, but do not report successful
        # cleanup when descendants could still be alive in the process group.
        if process.returncode is None:
            with suppress(ProcessLookupError):
                process.kill()
        with suppress(TimeoutError):
            await asyncio.wait_for(process.wait(), timeout=5)
        raise WorkerSecurityScanError("scanner_process_group_cleanup_failed") from exc
    with suppress(TimeoutError):
        await asyncio.wait_for(process.wait(), timeout=5)


async def _cancel_and_drain(tasks: tuple[asyncio.Task[Any], ...]) -> None:
    for task in tasks:
        if not task.done():
            task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)


async def _execute_bounded(argv: list[str], *, cwd: Path, env: dict[str, str], stdout_limit: int, stderr_limit: int) -> dict[str, Any]:
    """Execute one pinned scanner without a shell and with bounded output."""
    uid: int | None = None
    gid: int | None = None
    if os.name == "posix" and os.geteuid() == 0:
        account = pwd.getpwnam("nobody")
        uid, gid = account.pw_uid, account.pw_gid
    process_options: dict[str, Any] = {
        "cwd": str(cwd),
        "env": env,
        "stdout": asyncio.subprocess.PIPE,
        "stderr": asyncio.subprocess.PIPE,
        "start_new_session": True,
    }
    if uid is not None and gid is not None:
        process_options.update({"user": uid, "group": gid, "extra_groups": []})
    try:
        process = await asyncio.create_subprocess_exec(
            *argv,
            **process_options,
        )
    except (OSError, ValueError):
        return {"exit_code": None, "stdout": b"", "stderr": b"", "error": "scanner_start_failed"}
    assert process.stdout is not None and process.stderr is not None
    stdout_task = asyncio.create_task(_capture_bounded(process.stdout, stdout_limit))
    stderr_task = asyncio.create_task(_capture_bounded(process.stderr, stderr_limit))
    wait_task = asyncio.create_task(process.wait())
    try:
        stdout, stderr, exit_code = await asyncio.wait_for(
            asyncio.gather(stdout_task, stderr_task, wait_task),
            timeout=MAX_SCANNER_TIMEOUT_SECONDS,
        )
    except WorkerSecurityScanError as exc:
        tasks = (stdout_task, stderr_task, wait_task)
        try:
            await _kill_process_group(process)
        except WorkerSecurityScanError as cleanup_error:
            error = cleanup_error.code
        else:
            error = exc.code
        await _cancel_and_drain(tasks)
        return {"exit_code": None, "stdout": b"", "stderr": b"", "error": error}
    except TimeoutError:
        tasks = (stdout_task, stderr_task, wait_task)
        try:
            await _kill_process_group(process)
        except WorkerSecurityScanError as cleanup_error:
            error = cleanup_error.code
        else:
            error = "scanner_timeout"
        await _cancel_and_drain(tasks)
        return {"exit_code": None, "stdout": b"", "stderr": b"", "error": error}
    except OSError:
        tasks = (stdout_task, stderr_task, wait_task)
        try:
            await _kill_process_group(process)
        except WorkerSecurityScanError as cleanup_error:
            error = cleanup_error.code
        else:
            error = "scanner_io_error"
        await _cancel_and_drain(tasks)
        return {"exit_code": None, "stdout": b"", "stderr": b"", "error": error}
    except asyncio.CancelledError:
        # Cancellation must not orphan scanner processes. Shield cleanup so a
        # repeated cancellation does not interrupt the process-group signal.
        cleanup_task = asyncio.create_task(
            _cleanup_scanner_process_group(
                process,
                (stdout_task, stderr_task, wait_task),
            )
        )
        with suppress(asyncio.CancelledError):
            await asyncio.shield(cleanup_task)
        # The shielded cleanup continues in the event loop if cancellation is
        # repeated; preserve the caller's cancellation as the observable result.
        raise
    return {"exit_code": exit_code, "stdout": stdout, "stderr": stderr, "error": None}


async def _cleanup_scanner_process_group(
    process: asyncio.subprocess.Process,
    tasks: tuple[asyncio.Task[Any], ...],
) -> None:
    try:
        await _kill_process_group(process)
    finally:
        await _cancel_and_drain(tasks)


def _scan_environment(home: Path, temp: Path) -> dict[str, str]:
    return {
        "PATH": "/usr/local/bin:/usr/bin:/bin",
        "HOME": str(home),
        "TMPDIR": str(temp),
        "XDG_CACHE_HOME": str(home / ".cache"),
        "LC_ALL": "C",
        "LANG": "C",
        "NO_COLOR": "1",
        "SEMGREP_ENABLE_VERSION_CHECK": "0",
        "SEMGREP_SEND_METRICS": "off",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_TERMINAL_PROMPT": "0",
        # A clean environment avoids credential/proxy leakage and disables
        # configured telemetry, but does not restrict direct network access.
    }


def _scanner_finding_path(raw_path: Any) -> str | None:
    if not isinstance(raw_path, str):
        return None
    basename = Path(raw_path).name
    if basename == _SOURCE_FILENAME:
        return _SOURCE_RELATIVE_PATH
    if basename == _DOCKERFILE_FILENAME:
        return _DOCKERFILE_RELATIVE_PATH
    return None


def _finding_record(scanner: str, rule_id: Any, path: str, line: Any, severity: Any, ordinal: int) -> dict[str, Any]:
    safe_line = line if isinstance(line, int) and 0 <= line <= 100_000 else None
    safe_rule = str(rule_id or "unknown")[:200]
    safe_severity = str(severity or "UNKNOWN").upper()[:32]
    key = f"{scanner}\0{safe_rule}\0{path}\0{safe_line}\0{ordinal}".encode()
    return {
        "finding_id": hashlib.sha256(key).hexdigest(),
        "rule_id": safe_rule,
        "path": path,
        "line": safe_line,
        "severity": safe_severity,
    }


def _semgrep_findings(stdout: bytes) -> tuple[list[dict[str, Any]], int, list[str]]:
    try:
        value = json.loads(stdout)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return [], 0, ["semgrep_output_invalid_json"]
    if not isinstance(value, dict) or not isinstance(value.get("results"), list) or not isinstance(value.get("errors"), list):
        return [], 0, ["semgrep_output_shape_invalid"]
    findings: list[dict[str, Any]] = []
    errors: list[str] = []
    paths = value.get("paths")
    scanned_paths = paths.get("scanned") if isinstance(paths, dict) else None
    if not isinstance(scanned_paths, list) or any(
        not isinstance(path, str) for path in scanned_paths
    ):
        errors.append("semgrep_scanned_paths_invalid")
    elif not any(Path(path).name == _SOURCE_FILENAME for path in scanned_paths):
        errors.append("semgrep_source_not_scanned")
    total = len(value["results"])
    for ordinal, row in enumerate(value["results"]):
        if not isinstance(row, dict):
            errors.append("semgrep_result_shape_invalid")
            continue
        path = _scanner_finding_path(row.get("path"))
        start = row.get("start")
        extra = row.get("extra")
        if path is None or not isinstance(start, dict) or not isinstance(extra, dict):
            errors.append("semgrep_finding_location_invalid")
            continue
        if ordinal < MAX_FINDINGS_PER_SCANNER:
            findings.append(
                _finding_record(
                    "semgrep",
                    row.get("check_id"),
                    path,
                    start.get("line"),
                    extra.get("severity"),
                    ordinal,
                )
            )
    if len(value["errors"]) > 0:
        errors.append("semgrep_engine_reported_errors")
    if total > MAX_FINDINGS_PER_SCANNER:
        errors.append("semgrep_finding_limit_exceeded")
    return findings, total, sorted(set(errors))


def _trufflehog_findings(stdout: bytes) -> tuple[list[dict[str, Any]], int, list[str]]:
    findings: list[dict[str, Any]] = []
    errors: list[str] = []
    total = 0
    for ordinal, raw_line in enumerate(stdout.splitlines()):
        if not raw_line.strip():
            continue
        try:
            row = json.loads(raw_line)
        except (UnicodeDecodeError, json.JSONDecodeError):
            errors.append("trufflehog_output_invalid_jsonl")
            continue
        if not isinstance(row, dict):
            errors.append("trufflehog_result_shape_invalid")
            continue
        metadata = row.get("SourceMetadata")
        data = metadata.get("Data") if isinstance(metadata, dict) else None
        filesystem = data.get("Filesystem") if isinstance(data, dict) else None
        if not isinstance(filesystem, dict):
            errors.append("trufflehog_finding_location_invalid")
            total += 1
            continue
        path = _scanner_finding_path(filesystem.get("file"))
        if path is None:
            errors.append("trufflehog_finding_path_out_of_scope")
            total += 1
            continue
        total += 1
        if len(findings) < MAX_FINDINGS_PER_SCANNER:
            # Deliberately omit Raw, RawV2, verification response, source
            # excerpts, and every other potentially secret-bearing field.
            findings.append(
                _finding_record(
                    "trufflehog",
                    row.get("DetectorName") or row.get("DetectorType"),
                    path,
                    filesystem.get("line"),
                    "SECRET_DETECTION",
                    ordinal,
                )
            )
    if total > MAX_FINDINGS_PER_SCANNER:
        errors.append("trufflehog_finding_limit_exceeded")
    return findings, total, sorted(set(errors))


async def _one_scanner(
    *,
    name: str,
    argv: list[str],
    source_dir: Path,
    home: Path,
    temp: Path,
) -> dict[str, Any]:
    pin = PINNED_SCANNERS[name]
    executable = str(pin["executable"])
    env = _scan_environment(home, temp)
    version_argv = [executable, "--version"]
    version_result = await _execute_bounded(
        version_argv,
        cwd=source_dir,
        env=env,
        stdout_limit=4096,
        stderr_limit=4096,
    )
    version_text = (version_result.get("stdout") or b"").decode("utf-8", errors="replace").strip()
    expected_version = str(pin["version"])
    parsed_version = version_text.splitlines()[0] if version_text else ""
    if name == "trufflehog" and parsed_version.startswith("trufflehog "):
        parsed_version = parsed_version.removeprefix("trufflehog ")
    errors: list[str] = []
    if version_result.get("error"):
        errors.append(str(version_result["error"]))
    if version_result.get("exit_code") != 0 or parsed_version != expected_version:
        errors.append("scanner_version_mismatch")

    if errors:
        return {
            "name": name,
            "version": parsed_version or None,
            "version_argv": version_argv,
            "version_output_sha256": _sha256_bytes(version_result.get("stdout") or b""),
            "argv": argv,
            "exit_code": version_result.get("exit_code"),
            "status": "blocked",
            "finding_count": 0,
            "scanner_error_count": len(set(errors)),
            "scanner_errors": sorted(set(errors)),
            "findings": [],
            "stdout_bytes": 0,
            "stderr_bytes": len(version_result.get("stderr") or b""),
            "result_sha256": _sha256_bytes(b""),
            "stderr_sha256": _sha256_bytes(version_result.get("stderr") or b""),
        }

    result = await _execute_bounded(
        argv,
        cwd=source_dir,
        env=env,
        stdout_limit=int(pin["max_stdout_bytes"]),
        stderr_limit=int(pin["max_stderr_bytes"]),
    )
    stdout = result.get("stdout") or b""
    stderr = result.get("stderr") or b""
    if name == "semgrep":
        findings, finding_count, parse_errors = _semgrep_findings(stdout)
        valid_finding_exit = result.get("exit_code") == 0
    else:
        findings, finding_count, parse_errors = _trufflehog_findings(stdout)
        # TruffleHog's documented finding exit code is 183. It is a finding
        # result only when parsed findings are present. Empty output with this
        # exit code is inconsistent evidence and must fail closed.
        valid_finding_exit = result.get("exit_code") in {0, 183}
    scanner_errors = list(parse_errors)
    if result.get("error"):
        scanner_errors.append(str(result["error"]))
    if (
        name == "trufflehog"
        and result.get("exit_code") == 183
        and finding_count == 0
    ):
        scanner_errors.append("trufflehog_findings_exit_without_findings")
    if not valid_finding_exit:
        scanner_errors.append("scanner_nonzero_exit_without_supported_findings")
    if len(stderr) > int(pin["max_stderr_bytes"]):
        scanner_errors.append("scanner_stderr_limit_exceeded")
    status = "pass"
    if scanner_errors:
        status = "blocked"
    elif finding_count:
        status = "findings_review_required"
    return {
        "name": name,
        "version": expected_version,
        "version_argv": version_argv,
        "version_output_sha256": _sha256_bytes(version_result.get("stdout") or b""),
        "argv": argv,
        "exit_code": result.get("exit_code"),
        "status": status,
        "finding_count": finding_count,
        "scanner_error_count": len(set(scanner_errors)),
        "scanner_errors": sorted(set(scanner_errors)),
        "findings": findings,
        "stdout_bytes": len(stdout),
        "stderr_bytes": len(stderr),
        "result_sha256": _sha256_bytes(stdout),
        "stderr_sha256": _sha256_bytes(stderr),
    }


async def run_first_party_worker_source_scan(
    *,
    worker: dict[str, Any],
    provenance: dict[str, Any],
) -> dict[str, Any]:
    """Scan the fixed local candidate source; never executes worker code."""
    _root, paths, config_path, _ = _repo_root_and_inputs()
    image_build_provenance = _load_first_party_image_build_provenance(_root)
    file_bytes = {
        name: _read_regular_file(path)
        for name, path in paths.items()
    }
    config_bytes = _read_regular_file(config_path)
    identity = _validate_manifest_and_registered_identity(
        manifest_bytes=file_bytes["manifest"],
        source_bytes=file_bytes["source"],
        dockerfile_bytes=file_bytes["dockerfile"],
        config_bytes=config_bytes,
        worker=worker,
        provenance=provenance,
    )

    report: dict[str, Any] = {
        "schema_version": SOURCE_BOUND_SCAN_SCHEMA,
        "status": "blocked",
        "finding_review_status": "not_evaluated",
        "worker_registry_id": str(worker.get("id") or ""),
        "candidate": {
            "worker_registry_id": str(worker.get("id") or ""),
            "manifest_worker_id": FIRST_PARTY_WORKER_ID,
            "version": FIRST_PARTY_WORKER_VERSION,
            "source_repository": FIRST_PARTY_SOURCE_REPOSITORY,
            "source_revision": FIRST_PARTY_SOURCE_REVISION,
            "oci_image_digest": FIRST_PARTY_IMAGE_DIGEST,
            "runtime_sha256": identity["runtime_sha256"],
            "dockerfile_sha256": identity["dockerfile_sha256"],
        },
        "manifest_sha256": identity["manifest_sha256"],
        "image_build_provenance": image_build_provenance,
        "source_files": [
            {"path": _SOURCE_RELATIVE_PATH, "sha256": identity["runtime_sha256"]},
            {"path": _DOCKERFILE_RELATIVE_PATH, "sha256": identity["dockerfile_sha256"]},
        ],
        "semgrep_config_path": str(config_path),
        "semgrep_config_source_path": str(config_path),
        "semgrep_config_sha256": identity["semgrep_config_sha256"],
        "semgrep_config_snapshot_mode": "0444",
        "scanner_network_isolation": "not_enforced_by_scanner_process",
        "scan_started_at": datetime.now(UTC).isoformat(),
        "scanner_source_dir": None,
        "scanners": [],
        "finding_count": 0,
        "scanner_error_count": 0,
    }

    base_tmp = Path(tempfile.mkdtemp(prefix="aiat-worker-security-scan-", dir="/tmp"))
    source_dir = base_tmp / "source"
    home = base_tmp / "home"
    temp = base_tmp / "tmp"
    try:
        base_tmp.chmod(0o700)
        source_dir.mkdir(mode=0o700)
        home.mkdir(mode=0o700)
        temp.mkdir(mode=0o700)
        config_snapshot = base_tmp / "semgrep-default.yml"
        config_snapshot.write_bytes(config_bytes)
        config_snapshot.chmod(0o444)
        if (
            _sha256_file(config_snapshot) != identity["semgrep_config_sha256"]
            or stat.S_IMODE(config_snapshot.stat().st_mode) != 0o444
        ):
            raise WorkerSecurityScanError("semgrep_config_snapshot_invalid")
        if os.name == "posix" and os.geteuid() == 0:
            nobody = pwd.getpwnam("nobody")
            os.chown(base_tmp, nobody.pw_uid, nobody.pw_gid)
            os.chown(source_dir, nobody.pw_uid, nobody.pw_gid)
            os.chown(home, nobody.pw_uid, nobody.pw_gid)
            os.chown(temp, nobody.pw_uid, nobody.pw_gid)
        (source_dir / _SOURCE_FILENAME).write_bytes(file_bytes["source"])
        (source_dir / _SOURCE_FILENAME).chmod(0o444)
        (source_dir / _DOCKERFILE_FILENAME).write_bytes(file_bytes["dockerfile"])
        (source_dir / _DOCKERFILE_FILENAME).chmod(0o444)
        source_dir.chmod(0o500)

        source_dir_arg = str(source_dir)
        report["scanner_source_dir"] = source_dir_arg
        report["semgrep_config_path"] = str(config_snapshot)
        semgrep_argv = [
            PINNED_SCANNERS["semgrep"]["executable"],
            "scan",
            "--config",
            str(config_snapshot),
            "--json",
            "--metrics=off",
            "--disable-version-check",
            "--no-git-ignore",
            "--quiet",
            source_dir_arg,
        ]
        trufflehog_argv = [
            PINNED_SCANNERS["trufflehog"]["executable"],
            "filesystem",
            "--json",
            "--no-update",
            "--no-verification",
            "--log-level=-1",
            source_dir_arg,
        ]
        report["scanners"] = [
            await _one_scanner(
                name="semgrep",
                argv=semgrep_argv,
                source_dir=source_dir,
                home=home,
                temp=temp,
            ),
            await _one_scanner(
                name="trufflehog",
                argv=trufflehog_argv,
                source_dir=source_dir,
                home=home,
                temp=temp,
            ),
        ]
        report["finding_count"] = sum(int(row.get("finding_count") or 0) for row in report["scanners"])
        report["scanner_error_count"] = sum(int(row.get("scanner_error_count") or 0) for row in report["scanners"])
        if report["scanner_error_count"]:
            report["status"] = "blocked"
        elif report["finding_count"]:
            report["status"] = "findings_review_required"
            report["finding_review_status"] = "requires_human_review"
        else:
            report["status"] = "passed"
            report["finding_review_status"] = "not_required"
        report["scan_completed_at"] = datetime.now(UTC).isoformat()
        return finalize_source_bound_scan_report(report)
    finally:
        shutil.rmtree(base_tmp, ignore_errors=True)

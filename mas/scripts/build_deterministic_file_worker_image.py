"""Build and verify the first-party deterministic worker OCI candidate.

The BuildKit attestation emitted here is useful local build evidence only.  It
is intentionally not described as signed, authenticated, or produced by a
trusted builder.  It must never grant worker admission by itself.
"""

from __future__ import annotations

import argparse
import base64
import gzip
import hashlib
import io
import json
import os
import re
import subprocess
import tarfile
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

MAS_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = MAS_ROOT.parent
WORKER_ID = "aiat_deterministic_file_worker"
WORKER_VERSION = "0.1.1"
SOURCE_REVISION = "1855a75fb17243d8298b84468372e7a6e496da46"
SOURCE_PATH = "mas/workers/aiat_deterministic_file_worker_process.py"
DOCKERFILE_PATH = "mas/workers/Dockerfile.aiat_deterministic_file_worker"
WORKER_FILENAME = "aiat_deterministic_file_worker_process.py"
IMAGE_FILENAME = "/opt/aiat-worker/worker.py"
BASE_IMAGE = "python:3.11-slim"
BASE_DIGEST = "sha256:90744cff8f32887f075c47d747a173ff333e9e98801667af93c357fa9f5e28ff"
SCHEMA_VERSION = "aiat.deterministic-worker.build-provenance.v1"
PREDICATE_TYPE = "https://slsa.dev/provenance/v1"
SAFE_SOURCE_RE = re.compile(r"^[0-9a-f]{40}$")
SAFE_SHA256_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
SAFE_BUILDER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


class ProvenanceError(ValueError):
    """The OCI image archive or its BuildKit evidence did not verify."""


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _digest(data: bytes) -> str:
    return f"sha256:{_sha256(data)}"


def _run(command: list[str], *, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=cwd,
        check=False,
        capture_output=True,
        text=True,
        timeout=1800,
    )


def _checked(command: list[str], *, cwd: Path | None = None) -> str:
    result = _run(command, cwd=cwd)
    if result.returncode:
        # Do not echo arbitrary tool output into retained evidence or reports.
        raise ProvenanceError(
            f"command failed ({Path(command[0]).name}, exit {result.returncode})"
        )
    return result.stdout.strip()


def _git_file(revision: str, path: str) -> bytes:
    result = subprocess.run(
        ["git", "show", f"{revision}:{path}"],
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        timeout=30,
    )
    if result.returncode:
        raise ProvenanceError(f"pinned source file is unavailable: {path}")
    return result.stdout


def _read_oci_archive(archive_path: Path) -> tuple[dict[str, Any], dict[str, bytes]]:
    """Read an OCI archive and validate all descriptors used by the report."""
    try:
        with tarfile.open(archive_path, mode="r:*") as archive:
            files = {
                member.name: archive.extractfile(member).read()
                for member in archive.getmembers()
                if member.isfile() and archive.extractfile(member) is not None
            }
    except (OSError, tarfile.TarError) as exc:
        raise ProvenanceError("OCI archive could not be read") from exc
    try:
        index = json.loads(files["index.json"])
        layout = json.loads(files["oci-layout"])
    except (KeyError, json.JSONDecodeError) as exc:
        raise ProvenanceError("OCI archive has no valid index/layout") from exc
    if layout.get("imageLayoutVersion") != "1.0.0":
        raise ProvenanceError("OCI archive layout version is unsupported")
    if index.get("mediaType") != "application/vnd.oci.image.index.v1+json":
        raise ProvenanceError("OCI archive root index has an unexpected media type")
    if len(index.get("manifests", [])) != 1:
        raise ProvenanceError("OCI archive must contain one BuildKit output index")
    return index, files


def _blob(files: dict[str, bytes], descriptor: dict[str, Any]) -> bytes:
    digest = descriptor.get("digest")
    if not isinstance(digest, str) or not SAFE_SHA256_RE.fullmatch(digest):
        raise ProvenanceError("OCI descriptor digest is malformed")
    data = files.get(f"blobs/sha256/{digest.split(':', 1)[1]}")
    if data is None or _digest(data) != digest:
        raise ProvenanceError("OCI descriptor does not match retained blob bytes")
    if descriptor.get("size") != len(data):
        raise ProvenanceError("OCI descriptor size does not match retained blob bytes")
    return data


def _extract_statement(files: dict[str, bytes], attestation: dict[str, Any]) -> tuple[dict[str, Any], bytes]:
    manifest_bytes = _blob(files, attestation)
    try:
        manifest = json.loads(manifest_bytes)
    except json.JSONDecodeError as exc:
        raise ProvenanceError("OCI attestation manifest is malformed") from exc
    if manifest.get("artifactType") != "application/vnd.docker.attestation.manifest.v1+json":
        raise ProvenanceError("OCI entry is not a BuildKit attestation manifest")
    if len(manifest.get("layers", [])) != 1:
        raise ProvenanceError("BuildKit attestation must contain one SLSA statement")
    layer = manifest["layers"][0]
    if layer.get("mediaType") != "application/vnd.in-toto+json":
        raise ProvenanceError("BuildKit attestation layer is not in-toto JSON")
    if (layer.get("annotations") or {}).get("in-toto.io/predicate-type") != PREDICATE_TYPE:
        raise ProvenanceError("BuildKit attestation predicate type is unexpected")
    statement_bytes = _blob(files, layer)
    try:
        statement = json.loads(statement_bytes)
    except json.JSONDecodeError as exc:
        raise ProvenanceError("SLSA statement is malformed") from exc
    if statement.get("_type") != "https://in-toto.io/Statement/v1":
        raise ProvenanceError("SLSA statement type is unexpected")
    if statement.get("predicateType") != PREDICATE_TYPE:
        raise ProvenanceError("SLSA predicate type is unexpected")
    return manifest, statement_bytes


def _embedded_worker_sha(files: dict[str, bytes], image_manifest: dict[str, Any]) -> str:
    worker_bytes: bytes | None = None
    for layer in image_manifest.get("layers", []):
        layer_bytes = _blob(files, layer)
        if "gzip" in layer.get("mediaType", ""):
            layer_bytes = gzip.decompress(layer_bytes)
        try:
            with tarfile.open(fileobj=io.BytesIO(layer_bytes), mode="r:") as tar:
                for member in tar.getmembers():
                    if member.name.lstrip("./") == IMAGE_FILENAME.lstrip("/"):
                        stream = tar.extractfile(member)
                        if stream is None:
                            raise ProvenanceError("embedded worker image entry is not a file")
                        worker_bytes = stream.read()
        except tarfile.TarError as exc:
            raise ProvenanceError("image layer is not a valid tar stream") from exc
    if worker_bytes is None:
        raise ProvenanceError("embedded worker source was not found in image layers")
    return _sha256(worker_bytes)


def inspect_archive(archive_path: Path, *, metadata_path: Path | None = None) -> dict[str, Any]:
    """Inspect the OCI archive and return source/build/image binding fields."""
    archive_path = archive_path.resolve()
    _, files = _read_oci_archive(archive_path)
    root_index = json.loads(files["index.json"])
    output_descriptor = root_index["manifests"][0]
    output_index_bytes = _blob(files, output_descriptor)
    output_index = json.loads(output_index_bytes)
    if output_index.get("mediaType") != "application/vnd.oci.image.index.v1+json":
        raise ProvenanceError("BuildKit output is not an OCI image index")
    images = [
        row for row in output_index.get("manifests", [])
        if row.get("platform", {}).get("os") == "linux"
        and row.get("platform", {}).get("architecture") == "amd64"
    ]
    attestations = [
        row for row in output_index.get("manifests", [])
        if (row.get("annotations") or {}).get("vnd.docker.reference.type")
        == "attestation-manifest"
    ]
    if len(images) != 1 or len(attestations) != 1:
        raise ProvenanceError("OCI output must contain one linux/amd64 image and one attestation")
    image_descriptor, attestation_descriptor = images[0], attestations[0]
    image_manifest_bytes = _blob(files, image_descriptor)
    image_manifest = json.loads(image_manifest_bytes)
    attestation_manifest, statement_bytes = _extract_statement(files, attestation_descriptor)
    if attestation_manifest.get("subject", {}).get("digest") != image_descriptor.get("digest"):
        raise ProvenanceError("attestation subject does not bind the platform image manifest")
    statement = json.loads(statement_bytes)
    predicate = statement.get("predicate") or {}
    definition = predicate.get("buildDefinition") or {}
    dependencies = definition.get("resolvedDependencies") or []
    materials = [
        item for item in dependencies
        if item.get("digest", {}).get("sha256") == BASE_DIGEST.removeprefix("sha256:")
    ]
    if len(materials) != 1:
        raise ProvenanceError("SLSA materials do not identify the pinned Python base image")
    params = definition.get("externalParameters") or {}
    request = params.get("request") or {}
    args = request.get("args") or {}
    source_revision = args.get("build-arg:AIAT_WORKER_SOURCE_REVISION")
    source_sha = args.get("build-arg:AIAT_WORKER_SOURCE_SHA256")
    if not isinstance(source_revision, str) or not SAFE_SOURCE_RE.fullmatch(source_revision):
        raise ProvenanceError("SLSA build args do not contain an exact source revision")
    if not isinstance(source_sha, str) or not re.fullmatch(r"[0-9a-f]{64}", source_sha):
        raise ProvenanceError("SLSA build args do not contain a source hash")
    config_bytes = _blob(files, image_manifest["config"])
    config = json.loads(config_bytes)
    labels = (config.get("config") or {}).get("Labels") or {}
    if labels.get("org.opencontainers.image.revision") != source_revision:
        raise ProvenanceError("image revision label differs from SLSA build arg")
    if labels.get("com.aiat.worker.source-sha256") != source_sha:
        raise ProvenanceError("image source hash label differs from SLSA build arg")
    if labels.get("org.opencontainers.image.version") != WORKER_VERSION:
        raise ProvenanceError("image version label differs from the candidate version")
    if _embedded_worker_sha(files, image_manifest) != source_sha:
        raise ProvenanceError("embedded worker bytes do not match SLSA source hash")
    recipe_info = next(
        (
            item
            for item in ((predicate.get("runDetails") or {}).get("metadata") or {})
            .get("buildkit_metadata", {}).get("source", {}).get("infos", [])
            if item.get("filename") == Path(DOCKERFILE_PATH).name
        ),
        None,
    )
    if not recipe_info or not isinstance(recipe_info.get("data"), str):
        raise ProvenanceError("SLSA statement does not retain the Dockerfile source")
    try:
        recipe_bytes = base64.b64decode(recipe_info["data"], validate=True)
    except ValueError as exc:
        raise ProvenanceError("SLSA Dockerfile source is invalid base64") from exc
    dockerfile_hash = _sha256(recipe_bytes)
    if BASE_IMAGE.encode() not in recipe_bytes or BASE_DIGEST.encode() not in recipe_bytes:
        raise ProvenanceError("retained Dockerfile does not pin the expected base image")

    if metadata_path is not None:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        if metadata.get("containerimage.digest") != output_descriptor.get("digest"):
            raise ProvenanceError("Buildx metadata digest differs from OCI output index")

    context_digest = _sha256(
        b"aiat-deterministic-worker-context-v1\0"
        + WORKER_FILENAME.encode()
        + b"\0"
        + bytes.fromhex(source_sha)
    )
    archive_sha = _sha256(archive_path.read_bytes())
    return {
        "source_revision": source_revision,
        "source_sha256": source_sha,
        "context_sha256": context_digest,
        "recipe_sha256": dockerfile_hash,
        "base_image": BASE_IMAGE,
        "base_digest": BASE_DIGEST,
        "oci_index_digest": output_descriptor["digest"],
        "platform_manifest_digest": image_descriptor["digest"],
        "config_digest": image_manifest["config"]["digest"],
        "attestation_manifest_digest": attestation_descriptor["digest"],
        "attestation_subject_digest": attestation_manifest["subject"]["digest"],
        "predicate_type": statement["predicateType"],
        "statement_sha256": _sha256(statement_bytes),
        "statement": statement,
        "oci_index_raw_base64": base64.b64encode(output_index_bytes).decode("ascii"),
        "platform_manifest_raw_base64": base64.b64encode(image_manifest_bytes).decode("ascii"),
        "attestation_manifest_raw_base64": base64.b64encode(
            _blob(files, attestation_descriptor)
        ).decode("ascii"),
        "statement_raw_base64": base64.b64encode(statement_bytes).decode("ascii"),
        "builder_id": ((predicate.get("runDetails") or {}).get("builder") or {}).get("id", ""),
        "embedded_source_sha256": _embedded_worker_sha(files, image_manifest),
        "archive_sha256": archive_sha,
        "archive_path": str(archive_path),
    }


def verify_evidence(evidence: dict[str, Any], archive_path: Path) -> dict[str, Any]:
    """Recompute image and attestation facts; reject any mismatched report field."""
    observed = inspect_archive(archive_path)
    expected = evidence.get("build")
    if not isinstance(expected, dict):
        raise ProvenanceError("evidence has no build object")
    for key in (
        "source_revision", "source_sha256", "context_sha256", "recipe_sha256",
        "base_image", "base_digest", "oci_index_digest", "platform_manifest_digest",
        "config_digest", "attestation_manifest_digest", "attestation_subject_digest",
        "predicate_type", "statement_sha256", "builder_id", "embedded_source_sha256",
        "archive_sha256", "archive_path",
    ):
        if expected.get(key) != observed.get(key):
            raise ProvenanceError(f"evidence mismatch: {key}")
    if evidence.get("source", {}).get("revision") != observed["source_revision"]:
        raise ProvenanceError("evidence mismatch: source.revision")
    if evidence.get("source", {}).get("repository") != "https://github.com/Maaroufabousaleh/AIAT":
        raise ProvenanceError("evidence mismatch: source.repository")
    if evidence.get("source", {}).get("path") != SOURCE_PATH:
        raise ProvenanceError("evidence mismatch: source.path")
    if evidence.get("source", {}).get("sha256") != observed["source_sha256"]:
        raise ProvenanceError("evidence mismatch: source.sha256")
    if evidence.get("source", {}).get("context_sha256") != observed["context_sha256"]:
        raise ProvenanceError("evidence mismatch: source.context_sha256")
    if evidence.get("recipe", {}).get("path") != DOCKERFILE_PATH:
        raise ProvenanceError("evidence mismatch: recipe.path")
    if evidence.get("recipe", {}).get("sha256") != observed["recipe_sha256"]:
        raise ProvenanceError("evidence mismatch: recipe.sha256")
    if evidence.get("candidate") != {"worker_id": WORKER_ID, "version": WORKER_VERSION}:
        raise ProvenanceError("evidence mismatch: candidate identity")
    if evidence.get("image", {}).get("oci_index_digest") != observed["oci_index_digest"]:
        raise ProvenanceError("evidence mismatch: image.oci_index_digest")
    image = evidence.get("image") or {}
    if image.get("platform") != "linux/amd64":
        raise ProvenanceError("evidence mismatch: image.platform")
    if image.get("platform_manifest_digest") != observed["platform_manifest_digest"]:
        raise ProvenanceError("evidence mismatch: image.platform_manifest_digest")
    if image.get("runtime_reference") != (
        "aiat-deterministic-file-worker@" + observed["oci_index_digest"]
    ):
        raise ProvenanceError("evidence mismatch: image.runtime_reference")
    if image.get("embedded_source_sha256") != observed["embedded_source_sha256"]:
        raise ProvenanceError("evidence mismatch: image.embedded_source_sha256")
    if evidence.get("attestation", {}).get("statement") != observed["statement"]:
        raise ProvenanceError("evidence mismatch: attestation.statement")
    attestation = evidence.get("attestation") or {}
    for field, key in (
        ("manifest_digest", "attestation_manifest_digest"),
        ("subject_digest", "attestation_subject_digest"),
        ("predicate_type", "predicate_type"),
    ):
        if attestation.get(field) != observed[key]:
            raise ProvenanceError(f"evidence mismatch: attestation.{field}")
    if not attestation.get("format", "").endswith("unsigned"):
        raise ProvenanceError("evidence must state that the attestation is unsigned")
    for key in (
        "oci_index_raw_base64", "platform_manifest_raw_base64",
        "attestation_manifest_raw_base64", "statement_raw_base64",
    ):
        if expected.get(key) != observed.get(key):
            raise ProvenanceError(f"evidence mismatch: {key}")
    qualification = evidence.get("qualification") or {}
    if (
        qualification.get("signed") is not False
        or qualification.get("builder_authenticated") is not False
        or qualification.get("trusted_builder_claimed") is not False
        or qualification.get("admission_granted") is not False
    ):
        raise ProvenanceError("evidence must not claim signatures, trusted builder, or admission")
    builder = evidence.get("builder") or {}
    builder_name = builder.get("builder_name")
    if not isinstance(builder_name, str) or not SAFE_BUILDER_RE.fullmatch(builder_name):
        raise ProvenanceError("evidence builder name is malformed")
    if (
        builder.get("authenticated") != "false"
        or builder.get("trust") != "unauthenticated-local-builder"
        or builder.get("driver") != "docker"
    ):
        raise ProvenanceError("evidence builder trust scope is inconsistent")
    if (evidence.get("invocation") or {}).get("argv") != _sanitized_argv(
        builder_name, observed["source_sha256"], observed["source_revision"]
    ):
        raise ProvenanceError("evidence mismatch: invocation.argv")
    invocation = evidence.get("invocation") or {}
    expected_substitutions = {
        "<detached-checkout>": "clean worktree at the exact source commit",
        "<temporary-context>": "single-file context containing only worker source",
        "<temporary-build-output>": "Buildx metadata file",
        "<requested-archive-output>": str(archive_path.resolve()),
    }
    if invocation.get("path_substitutions") != expected_substitutions:
        raise ProvenanceError("evidence mismatch: invocation.path_substitutions")
    if invocation.get("build_secrets") != [] or invocation.get("environment_files_read") != []:
        raise ProvenanceError("evidence reports unexpected build secrets or environment files")
    if observed["source_revision"] != SOURCE_REVISION:
        raise ProvenanceError("evidence source revision is not the pinned candidate commit")
    expected_source = _sha256(_git_file(SOURCE_REVISION, SOURCE_PATH))
    expected_recipe = _sha256(_git_file(SOURCE_REVISION, DOCKERFILE_PATH))
    if observed["source_sha256"] != expected_source:
        raise ProvenanceError("embedded source does not match the pinned Git commit")
    if observed["recipe_sha256"] != expected_recipe:
        raise ProvenanceError("retained recipe does not match the pinned Git commit")
    return {"status": "pass", "scope": "local unsigned BuildKit source/image binding", "build": observed}


def _docker_identity(builder: str) -> dict[str, str]:
    version = _checked(["docker", "version", "--format", "{{.Client.Version}}/{{.Server.Version}}"])
    buildx = _checked(["docker", "buildx", "version"])
    inspect = _checked(["docker", "buildx", "inspect", builder])
    match = re.search(r"BuildKit version:\s*(\S+)", inspect)
    return {
        "builder_name": builder,
        "driver": "docker",
        "docker_engine_client_server": version,
        "buildx_version": buildx,
        "buildkit_version": match.group(1) if match else "unreported",
        "authenticated": "false",
        "trust": "unauthenticated-local-builder",
    }


def _sanitized_argv(builder: str, source_sha: str, source_revision: str) -> list[str]:
    """Exact Buildx arguments with only ephemeral filesystem paths normalized."""
    return [
        "docker", "buildx", "build", "--builder", builder,
        "--platform", "linux/amd64", "--provenance=mode=max", "--sbom=false",
        "--metadata-file", "<temporary-build-output>/buildx-metadata.json",
        "--output", "type=oci,dest=<requested-archive-output>",
        "--file", "<detached-checkout>/" + DOCKERFILE_PATH,
        "--build-arg", f"AIAT_WORKER_SOURCE_SHA256={source_sha}",
        "--build-arg", f"AIAT_WORKER_SOURCE_REVISION={source_revision}",
        "<temporary-context>",
    ]


def build_candidate(
    *,
    source_revision: str,
    builder: str,
    archive_path: Path,
    evidence_path: Path,
) -> dict[str, Any]:
    if source_revision != SOURCE_REVISION:
        raise ProvenanceError("this candidate builder accepts only the pinned source revision")
    if not SAFE_BUILDER_RE.fullmatch(builder):
        raise ProvenanceError("builder name is malformed")
    if _checked(["git", "cat-file", "-t", source_revision], cwd=REPO_ROOT) != "commit":
        raise ProvenanceError("pinned source revision is not a Git commit")
    source = _git_file(source_revision, SOURCE_PATH)
    recipe = _git_file(source_revision, DOCKERFILE_PATH)
    source_sha = _sha256(source)
    if not source_sha == "3b75bea1aa232ac5362d46b448cdc789a55f6e87e955ce4053a7d849ac858784":
        raise ProvenanceError("pinned worker source hash differs from the expected candidate")
    archive_arg = Path(archive_path)
    evidence_arg = Path(evidence_path)
    if any(path.exists() or path.is_symlink() for path in (archive_arg, evidence_arg)):
        raise ProvenanceError("refusing to overwrite an existing archive or evidence file")
    archive_path = archive_arg.resolve()
    evidence_path = evidence_arg.resolve()
    if archive_path == evidence_path:
        raise ProvenanceError("archive and evidence outputs must use different paths")
    archive_path.parent.mkdir(parents=True, exist_ok=True)
    evidence_path.parent.mkdir(parents=True, exist_ok=True)
    builder_identity = _docker_identity(builder)

    with tempfile.TemporaryDirectory(prefix="aiat-deterministic-worker-build-") as temp:
        temp_path = Path(temp)
        checkout = temp_path / "source"
        context_dir = temp_path / "context"
        context_dir.mkdir()
        metadata_path = temp_path / "buildx-metadata.json"
        _checked(
            ["git", "worktree", "add", "--quiet", "--detach", str(checkout), source_revision],
            cwd=REPO_ROOT,
        )
        try:
            if _checked(["git", "rev-parse", "HEAD"], cwd=checkout) != source_revision:
                raise ProvenanceError("detached source checkout has the wrong revision")
            if _checked(["git", "status", "--porcelain"], cwd=checkout):
                raise ProvenanceError("detached source checkout is not clean")
            source_file = context_dir / WORKER_FILENAME
            source_file.write_bytes(source)
            # This staging context contains the single source file and nothing
            # from the operator worktree, environment, or ignored directories.
            revision_epoch = int(_checked(["git", "show", "-s", "--format=%ct", source_revision], cwd=REPO_ROOT))
            os.utime(source_file, (revision_epoch, revision_epoch))
            context_hash = _sha256(
                b"aiat-deterministic-worker-context-v1\0"
                + WORKER_FILENAME.encode()
                + b"\0"
                + bytes.fromhex(source_sha)
            )
            build_command = [
                "docker", "buildx", "build", "--builder", builder,
                "--platform", "linux/amd64", "--provenance=mode=max", "--sbom=false",
                "--metadata-file", str(metadata_path), "--output", f"type=oci,dest={archive_path}",
                "--file", str(checkout / DOCKERFILE_PATH),
                "--build-arg", f"AIAT_WORKER_SOURCE_SHA256={source_sha}",
                "--build-arg", f"AIAT_WORKER_SOURCE_REVISION={source_revision}",
                str(context_dir),
            ]
            result = _run(build_command, cwd=checkout)
            if result.returncode:
                raise ProvenanceError(f"BuildKit image build failed (exit {result.returncode})")
            build = inspect_archive(archive_path, metadata_path=metadata_path)
            if build["source_revision"] != source_revision:
                raise ProvenanceError("BuildKit provenance does not retain the requested source revision")
            if build["source_sha256"] != source_sha or build["recipe_sha256"] != _sha256(recipe):
                raise ProvenanceError("BuildKit image/provenance differs from the clean source snapshot")
            local_tag = f"aiat-deterministic-file-worker:provenance-{build['oci_index_digest'].split(':')[1][:12]}"
            load = _run(["docker", "load", "--input", str(archive_path)])
            if load.returncode:
                raise ProvenanceError(f"Docker image import failed (exit {load.returncode})")
            tagged = _run(
                ["docker", "image", "tag", build["oci_index_digest"], local_tag]
            )
            if tagged.returncode:
                raise ProvenanceError(f"Docker image tag failed (exit {tagged.returncode})")
            inspect = _run(
                [
                    "docker", "image", "inspect", local_tag, "--format",
                    "{{json .RepoDigests}}\n{{json .Config.Labels}}\n{{.Id}}",
                ]
            )
            if inspect.returncode:
                raise ProvenanceError("Docker could not inspect the imported candidate image")
            try:
                raw_digests, raw_labels, image_id = inspect.stdout.rstrip().splitlines()
                repo_digests = json.loads(raw_digests)
                labels = json.loads(raw_labels)
            except (ValueError, json.JSONDecodeError) as exc:
                raise ProvenanceError("Docker image inspection returned an invalid identity") from exc
            runtime_ref = f"aiat-deterministic-file-worker@{build['oci_index_digest']}"
            if runtime_ref not in repo_digests:
                raise ProvenanceError("Docker does not resolve the attested OCI index digest")
            if (
                labels.get("org.opencontainers.image.revision") != source_revision
                or labels.get("com.aiat.worker.source-sha256") != source_sha
            ):
                raise ProvenanceError("Docker imported labels do not match pinned source identity")
            evidence = {
                "schema_version": SCHEMA_VERSION,
                "candidate": {"worker_id": WORKER_ID, "version": WORKER_VERSION},
                "source": {
                    "repository": "https://github.com/Maaroufabousaleh/AIAT",
                    "revision": source_revision,
                    "path": SOURCE_PATH,
                    "sha256": source_sha,
                    "context_sha256": context_hash,
                    "context_entries": [WORKER_FILENAME],
                },
                "recipe": {"path": DOCKERFILE_PATH, "sha256": _sha256(recipe)},
                "builder": builder_identity,
                "invocation": {
                    "argv": _sanitized_argv(builder, source_sha, source_revision),
                    "path_substitutions": {
                        "<detached-checkout>": "clean worktree at the exact source commit",
                        "<temporary-context>": "single-file context containing only worker source",
                        "<temporary-build-output>": "Buildx metadata file",
                        "<requested-archive-output>": str(archive_path),
                    },
                    "build_secrets": [],
                    "environment_files_read": [],
                },
                "build": build,
                "image": {
                    "oci_index_digest": build["oci_index_digest"],
                    "platform": "linux/amd64",
                    "platform_manifest_digest": build["platform_manifest_digest"],
                    "runtime_reference": runtime_ref,
                    "local_tag": local_tag,
                    "docker_image_id": image_id,
                    "docker_repo_digests": repo_digests,
                    "docker_labels": labels,
                    "embedded_source_sha256": build["embedded_source_sha256"],
                },
                "attestation": {
                    "format": "OCI referrer-style SLSA v1; BuildKit generated; unsigned",
                    "manifest_digest": build["attestation_manifest_digest"],
                    "subject_digest": build["attestation_subject_digest"],
                    "predicate_type": build["predicate_type"],
                    "statement": build["statement"],
                    "statement_raw_base64": build["statement_raw_base64"],
                    "oci_index_raw_base64": build["oci_index_raw_base64"],
                    "platform_manifest_raw_base64": build["platform_manifest_raw_base64"],
                    "attestation_manifest_raw_base64": build["attestation_manifest_raw_base64"],
                },
                "qualification": {
                    "status": "local_build_binding_observed",
                    "signed": False,
                    "builder_authenticated": False,
                    "trusted_builder_claimed": False,
                    "admission_granted": False,
                    "scope": "isolated internal development candidate only",
                    "limitations": [
                        "BuildKit builder.id is empty and no signature was produced.",
                        "This evidence does not authenticate who operated the local builder.",
                        "This evidence does not grant security approval, stewardship, or activation.",
                        "The archive must be retained privately or pushed to an operator-selected registry to preserve bytes for later verification.",
                    ],
                },
                "created_at": datetime.now(UTC).isoformat(),
            }
            evidence_path.write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
            return evidence
        finally:
            _run(["git", "worktree", "remove", "--force", str(checkout)], cwd=REPO_ROOT)


def verify_file(evidence_path: Path, archive_path: Path) -> dict[str, Any]:
    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    if evidence.get("schema_version") != SCHEMA_VERSION:
        raise ProvenanceError("unsupported evidence schema")
    verified = verify_evidence(evidence, archive_path)
    build = verified["build"]
    return {
        "status": verified["status"],
        "scope": verified["scope"],
        "source_revision": build["source_revision"],
        "source_sha256": build["source_sha256"],
        "recipe_sha256": build["recipe_sha256"],
        "base_image": build["base_image"],
        "base_digest": build["base_digest"],
        "oci_index_digest": build["oci_index_digest"],
        "platform_manifest_digest": build["platform_manifest_digest"],
        "attestation_manifest_digest": build["attestation_manifest_digest"],
        "attestation_subject_digest": build["attestation_subject_digest"],
        "predicate_type": build["predicate_type"],
        "builder_id": build["builder_id"],
        "embedded_source_sha256": build["embedded_source_sha256"],
        "archive_sha256": build["archive_sha256"],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-revision", default=SOURCE_REVISION)
    parser.add_argument("--builder", default="aiat-wsl")
    parser.add_argument("--archive", type=Path, required=True, help="OCI archive output/input")
    parser.add_argument("--evidence", type=Path, required=True, help="provenance JSON output/input")
    parser.add_argument("--verify", action="store_true", help="verify an existing evidence/archive pair")
    args = parser.parse_args(argv)
    try:
        if args.verify:
            report = verify_file(args.evidence, args.archive)
        else:
            evidence = build_candidate(
                source_revision=args.source_revision,
                builder=args.builder,
                archive_path=args.archive,
                evidence_path=args.evidence,
            )
            report = {
                "status": "pass",
                "scope": evidence["qualification"]["scope"],
                "oci_index_digest": evidence["image"]["oci_index_digest"],
                "platform_manifest_digest": evidence["image"]["platform_manifest_digest"],
                "attestation_subject_digest": evidence["attestation"]["subject_digest"],
                "evidence_path": str(args.evidence.resolve()),
                "archive_path": str(args.archive.resolve()),
                "builder_trust": evidence["qualification"],
            }
        print(json.dumps(report, sort_keys=True))
        return 0
    except (OSError, json.JSONDecodeError, ProvenanceError, subprocess.TimeoutExpired) as exc:
        print(json.dumps({"status": "fail", "reason": str(exc)}, sort_keys=True))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

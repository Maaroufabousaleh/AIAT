from __future__ import annotations

import base64
import gzip
import hashlib
import importlib.util
import io
import json
import tarfile
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "build_deterministic_file_worker_image.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("worker_image_provenance", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _json_bytes(value: object) -> bytes:
    return json.dumps(value, separators=(",", ":"), sort_keys=True).encode()


def _hash(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _write_blob(blobs: dict[str, bytes], data: bytes, media_type: str) -> dict:
    digest = _hash(data)
    blobs[f"blobs/sha256/{digest.split(':', 1)[1]}"] = data
    return {"mediaType": media_type, "digest": digest, "size": len(data)}


def _tar_file(path: str, data: bytes) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as archive:
        member = tarfile.TarInfo(path)
        member.size = len(data)
        member.mtime = 1
        member.mode = 0o644
        archive.addfile(member, io.BytesIO(data))
    return buffer.getvalue()


def _make_oci_archive(
    tmp_path: Path, *, embedded_source_override: bytes | None = None
) -> tuple[Path, dict]:
    module = _load_module()
    source = module._git_file(module.SOURCE_REVISION, module.SOURCE_PATH)
    recipe = module._git_file(module.SOURCE_REVISION, module.DOCKERFILE_PATH)
    source_sha = hashlib.sha256(source).hexdigest()
    blobs: dict[str, bytes] = {}

    config = {
        "created": "2026-01-01T00:00:00Z",
        "config": {
            "Labels": {
                "org.opencontainers.image.revision": module.SOURCE_REVISION,
                "com.aiat.worker.source-sha256": source_sha,
                "org.opencontainers.image.version": module.WORKER_VERSION,
            }
        },
        "rootfs": {"type": "layers", "diff_ids": [_hash(source)]},
    }
    config_descriptor = _write_blob(
        blobs, _json_bytes(config), "application/vnd.oci.image.config.v1+json"
    )
    embedded_source = source if embedded_source_override is None else embedded_source_override
    layer = gzip.compress(_tar_file("opt/aiat-worker/worker.py", embedded_source), mtime=0)
    layer_descriptor = _write_blob(
        blobs, layer, "application/vnd.oci.image.layer.v1.tar+gzip"
    )
    image_manifest = {
        "schemaVersion": 2,
        "mediaType": "application/vnd.oci.image.manifest.v1+json",
        "config": config_descriptor,
        "layers": [layer_descriptor],
    }
    image_bytes = _json_bytes(image_manifest)
    image_descriptor = _write_blob(
        blobs, image_bytes, "application/vnd.oci.image.manifest.v1+json"
    )
    image_descriptor["platform"] = {"os": "linux", "architecture": "amd64"}

    statement = {
        "_type": "https://in-toto.io/Statement/v1",
        "predicateType": module.PREDICATE_TYPE,
        "subject": [],
        "predicate": {
            "buildDefinition": {
                "resolvedDependencies": [
                    {
                        "uri": "pkg:docker/python@3.11-slim",
                        "digest": {"sha256": module.BASE_DIGEST.split(":", 1)[1]},
                    }
                ],
                "externalParameters": {
                    "request": {
                        "args": {
                            "build-arg:AIAT_WORKER_SOURCE_REVISION": module.SOURCE_REVISION,
                            "build-arg:AIAT_WORKER_SOURCE_SHA256": source_sha,
                        }
                    }
                },
            },
            "runDetails": {
                "builder": {"id": ""},
                "metadata": {
                    "buildkit_metadata": {
                        "source": {
                            "infos": [
                                {
                                    "filename": Path(module.DOCKERFILE_PATH).name,
                                    "data": base64.b64encode(recipe).decode(),
                                }
                            ]
                        }
                    }
                },
            },
        },
    }
    statement_bytes = _json_bytes(statement)
    statement_descriptor = _write_blob(
        blobs, statement_bytes, "application/vnd.in-toto+json"
    )
    statement_descriptor["annotations"] = {
        "in-toto.io/predicate-type": module.PREDICATE_TYPE
    }
    attestation_manifest = {
        "schemaVersion": 2,
        "mediaType": "application/vnd.oci.image.manifest.v1+json",
        "artifactType": "application/vnd.docker.attestation.manifest.v1+json",
        "config": {
            "mediaType": "application/vnd.oci.empty.v1+json",
            "digest": _hash(b"{}"),
            "size": 2,
            "data": "e30=",
        },
        "layers": [statement_descriptor],
        "subject": {
            "mediaType": image_descriptor["mediaType"],
            "digest": image_descriptor["digest"],
            "size": image_descriptor["size"],
        },
    }
    attestation_bytes = _json_bytes(attestation_manifest)
    attestation_descriptor = _write_blob(
        blobs, attestation_bytes, "application/vnd.oci.image.manifest.v1+json"
    )
    attestation_descriptor["artifactType"] = "application/vnd.docker.attestation.manifest.v1+json"
    attestation_descriptor["annotations"] = {
        "vnd.docker.reference.digest": image_descriptor["digest"],
        "vnd.docker.reference.type": "attestation-manifest",
    }
    attestation_descriptor["platform"] = {"os": "unknown", "architecture": "unknown"}

    output_index = {
        "schemaVersion": 2,
        "mediaType": "application/vnd.oci.image.index.v1+json",
        "manifests": [image_descriptor, attestation_descriptor],
    }
    output_index_bytes = _json_bytes(output_index)
    output_index_descriptor = _write_blob(
        blobs, output_index_bytes, "application/vnd.oci.image.index.v1+json"
    )
    root_index = {
        "schemaVersion": 2,
        "mediaType": "application/vnd.oci.image.index.v1+json",
        "manifests": [output_index_descriptor],
    }
    blobs["index.json"] = _json_bytes(root_index)
    blobs["oci-layout"] = _json_bytes({"imageLayoutVersion": "1.0.0"})

    archive_path = tmp_path / "worker.oci.tar"
    with tarfile.open(archive_path, "w") as archive:
        for name, data in sorted(blobs.items()):
            member = tarfile.TarInfo(name)
            member.size = len(data)
            member.mtime = 1
            member.mode = 0o644
            archive.addfile(member, io.BytesIO(data))
    if embedded_source_override is not None:
        return archive_path, {}
    inspected = module.inspect_archive(archive_path)
    evidence = {
        "schema_version": module.SCHEMA_VERSION,
        "candidate": {"worker_id": module.WORKER_ID, "version": module.WORKER_VERSION},
        "source": {
            "repository": "https://github.com/Maaroufabousaleh/AIAT",
            "revision": inspected["source_revision"],
            "path": module.SOURCE_PATH,
            "sha256": inspected["source_sha256"],
            "context_sha256": inspected["context_sha256"],
        },
        "recipe": {"path": module.DOCKERFILE_PATH, "sha256": inspected["recipe_sha256"]},
        "builder": {
            "builder_name": "aiat-wsl",
            "driver": "docker",
            "authenticated": "false",
            "trust": "unauthenticated-local-builder",
        },
        "invocation": {
            "argv": module._sanitized_argv(
                "aiat-wsl", inspected["source_sha256"], inspected["source_revision"]
            ),
            "path_substitutions": {
                "<detached-checkout>": "clean worktree at the exact source commit",
                "<temporary-context>": "single-file context containing only worker source",
                "<temporary-build-output>": "Buildx metadata file",
                "<requested-archive-output>": str(archive_path.resolve()),
            },
            "build_secrets": [],
            "environment_files_read": [],
        },
        "image": {
            "oci_index_digest": inspected["oci_index_digest"],
            "platform": "linux/amd64",
            "platform_manifest_digest": inspected["platform_manifest_digest"],
            "runtime_reference": "aiat-deterministic-file-worker@" + inspected["oci_index_digest"],
            "embedded_source_sha256": inspected["embedded_source_sha256"],
        },
        "attestation": {
            "format": "OCI referrer-style SLSA v1; BuildKit generated; unsigned",
            "manifest_digest": inspected["attestation_manifest_digest"],
            "subject_digest": inspected["attestation_subject_digest"],
            "predicate_type": inspected["predicate_type"],
            "statement": inspected["statement"],
        },
        "qualification": {
            "signed": False,
            "builder_authenticated": False,
            "trusted_builder_claimed": False,
            "admission_granted": False,
        },
        "build": inspected,
    }
    return archive_path, evidence


def test_oci_buildkit_provenance_verifies_source_recipe_image_and_subject(tmp_path: Path) -> None:
    module = _load_module()
    archive_path, evidence = _make_oci_archive(tmp_path)

    report = module.verify_evidence(evidence, archive_path)

    assert report["status"] == "pass"
    assert report["build"]["source_revision"] == module.SOURCE_REVISION
    assert report["build"]["attestation_subject_digest"] == report["build"]["platform_manifest_digest"]
    assert report["build"]["embedded_source_sha256"] == report["build"]["source_sha256"]


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("build", "source_revision"), "0" * 40),
        (("build", "recipe_sha256"), "0" * 64),
        (("build", "oci_index_digest"), "sha256:" + "0" * 64),
        (("build", "statement_sha256"), "0" * 64),
        (("image", "oci_index_digest"), "sha256:" + "0" * 64),
        (("source", "repository"), "https://example.invalid/repo"),
        (("source", "path"), "other.py"),
        (("recipe", "path"), "Dockerfile.evil"),
        (("recipe", "sha256"), "0" * 64),
        (("image", "platform_manifest_digest"), "sha256:" + "0" * 64),
        (("image", "runtime_reference"), "worker:latest"),
        (("attestation", "manifest_digest"), "sha256:" + "0" * 64),
        (("attestation", "subject_digest"), "sha256:" + "0" * 64),
        (("attestation", "predicate_type"), "https://example.invalid/predicate"),
        (("invocation", "path_substitutions"), {}),
    ],
)
def test_tampered_source_recipe_image_or_provenance_is_rejected(
    tmp_path: Path, path: tuple[str, str], value: str
) -> None:
    module = _load_module()
    archive_path, evidence = _make_oci_archive(tmp_path)
    target = evidence
    for part in path[:-1]:
        target = target[part]
    target[path[-1]] = value

    with pytest.raises(module.ProvenanceError):
        module.verify_evidence(evidence, archive_path)


def test_tampered_embedded_worker_bytes_are_rejected(tmp_path: Path) -> None:
    module = _load_module()
    archive_path, _ = _make_oci_archive(
        tmp_path, embedded_source_override=b"tampered worker source"
    )

    with pytest.raises(module.ProvenanceError):
        module.inspect_archive(archive_path)


def test_builder_refuses_to_overwrite_existing_or_symlink_outputs(tmp_path: Path) -> None:
    module = _load_module()
    existing = tmp_path / "existing.oci.tar"
    existing.write_bytes(b"keep")
    evidence = tmp_path / "evidence.json"

    with pytest.raises(module.ProvenanceError, match="refusing to overwrite"):
        module.build_candidate(
            source_revision=module.SOURCE_REVISION,
            builder="aiat-wsl",
            archive_path=existing,
            evidence_path=evidence,
        )

    target = tmp_path / "target-does-not-exist"
    symlink = tmp_path / "evidence-link.json"
    symlink.symlink_to(target)
    with pytest.raises(module.ProvenanceError, match="refusing to overwrite"):
        module.build_candidate(
            source_revision=module.SOURCE_REVISION,
            builder="aiat-wsl",
            archive_path=tmp_path / "new.oci.tar",
            evidence_path=symlink,
        )


def test_builder_refuses_same_archive_and_evidence_path(tmp_path: Path) -> None:
    module = _load_module()
    output = tmp_path / "same-output"

    with pytest.raises(module.ProvenanceError, match="different paths"):
        module.build_candidate(
            source_revision=module.SOURCE_REVISION,
            builder="aiat-wsl",
            archive_path=output,
            evidence_path=output,
        )

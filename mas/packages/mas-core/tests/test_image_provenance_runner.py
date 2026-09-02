"""Contract tests for static and live production-image evidence."""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import yaml

SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "check_image_provenance.py"
LOCK_EXAMPLE = Path(__file__).resolve().parents[3] / "infra" / "compose" / "production-image-lock.example.env"


def _load_runner():
    spec = importlib.util.spec_from_file_location("check_image_provenance", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _clean_environment() -> dict[str, str]:
    return {
        key: value
        for key, value in os.environ.items()
        if not key.endswith("_IMAGE_REF")
    }


def test_static_image_contract_emits_machine_readable_pass() -> None:
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--json"],
        check=False,
        capture_output=True,
        text=True,
        env=_clean_environment(),
    )

    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["schema_version"] == "aiat.image-provenance.v1"
    assert report["mode"] == "static"
    assert report["status"] == "pass"
    assert "live" not in report


def test_production_image_lock_example_covers_every_compose_image_input() -> None:
    runner = _load_runner()
    assert LOCK_EXAMPLE.is_file()
    values = runner._env_file(LOCK_EXAMPLE)
    expected = runner._compose_variables()

    assert set(values) == expected
    assert all(value.startswith("registry.example.invalid/") for value in values.values())
    assert all("<64-hex-digest>" in value for value in values.values())
    assert not any("password" in key.lower() or "secret" in key.lower() for key in values)


def test_live_image_identity_is_fail_closed_without_deployment_refs() -> None:
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--live", "--json"],
        check=False,
        capture_output=True,
        text=True,
        env=_clean_environment(),
    )

    assert result.returncode == 2, result.stderr
    report = json.loads(result.stdout)
    assert report["status"] == "pass"
    assert report["live"]["status"] == "blocked"
    assert report["live"]["scope"] == "local Docker RepoDigests identity only"


def test_live_image_identity_classifies_match_and_mismatch(monkeypatch) -> None:
    runner = _load_runner()
    rows = runner._inventory_rows()
    digest_by_id = {row["id"]: "sha256:" + ("a" * 64) for row in rows}
    env = {
        row["ref_env"]: f"registry.invalid/{row['id']}@{digest_by_id[row['id']]!s}"
        for row in rows
    }

    monkeypatch.setattr(runner, "_docker_engine_available", lambda: True)

    def inspect_match(reference: str):
        return [runner._digest_value(reference)], None

    monkeypatch.setattr(runner, "_inspect_image_digests", inspect_match)
    report = runner.inspect_live(env)
    assert report["status"] == "pass"
    assert all(image["status"] == "pass" for image in report["images"])

    mismatch_id = rows[0]["id"]

    def inspect_mismatch(reference: str):
        if mismatch_id in reference:
            return ["sha256:" + ("b" * 64)], None
        return [runner._digest_value(reference)], None

    monkeypatch.setattr(runner, "_inspect_image_digests", inspect_mismatch)
    report = runner.inspect_live(env)
    assert report["status"] == "fail"
    assert any(mismatch_id in error for error in report["errors"])


def test_require_sbom_validates_cyclonedx_structure(tmp_path: Path) -> None:
    runner = _load_runner()
    valid = tmp_path / "aiat-sbom.cdx.json"
    valid.write_text(
        json.dumps(
            {
                "bomFormat": "CycloneDX",
                "specVersion": "1.6",
                "metadata": {"component": {"type": "application", "name": "aiat"}},
                "components": [
                    {
                        "type": "library",
                        "name": "example",
                        "version": "1.0.0",
                        "bom-ref": "pkg:pypi/example@1.0.0",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    assert runner._validate_sbom_artifact(valid) is None

    malformed = tmp_path / "malformed.json"
    malformed.write_text(
        json.dumps(
            {
                "bomFormat": "CycloneDX",
                "specVersion": "1.6",
                "metadata": {"component": {"type": "application", "name": "aiat"}},
                "components": [
                    {"type": "library", "name": "example", "bom-ref": "same"},
                    {"type": "library", "name": "other", "bom-ref": "same"},
                ],
            }
        ),
        encoding="utf-8",
    )
    assert "duplicates bom-ref" in (runner._validate_sbom_artifact(malformed) or "")


def test_require_sbom_is_fail_closed_for_invalid_declared_artifact(
    monkeypatch, tmp_path: Path
) -> None:
    runner = _load_runner()
    sbom = tmp_path / "invalid-sbom.json"
    sbom.write_text("{\"bomFormat\":\"not-cyclonedx\"}", encoding="utf-8")
    scan = tmp_path / "scan.json"
    scan.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(
        runner,
        "_inventory_rows",
        lambda: [
            {
                "id": "fixture",
                "ref_env": "FIXTURE_IMAGE_REF",
                "sbom": str(sbom),
                "scan": str(scan),
            }
        ],
    )
    monkeypatch.setattr(runner, "_docker_engine_available", lambda: True)
    monkeypatch.setattr(
        runner,
        "_inspect_image_digests",
        lambda reference: ([runner._digest_value(reference)], None),
    )
    digest = "sha256:" + ("a" * 64)
    report = runner.inspect_live(
        {"FIXTURE_IMAGE_REF": f"registry.invalid/fixture@{digest}"},
        require_sbom=True,
    )
    assert report["status"] == "blocked"
    assert any("bomFormat" in error for error in report["errors"])


def test_static_inventory_rejects_duplicate_identity_and_malformed_metadata(tmp_path: Path) -> None:
    runner = _load_runner()
    inventory = yaml.safe_load(runner.IMAGE_INVENTORY_PATH.read_text(encoding="utf-8"))
    inventory["images"][1]["id"] = inventory["images"][0]["id"]
    inventory["images"][0]["oci_digest"] = "sha256:not-a-digest"
    inventory["images"][0]["lock_hash"] = "not-a-lock-hash"
    path = tmp_path / "production_images.yaml"
    path.write_text(yaml.safe_dump(inventory, sort_keys=False), encoding="utf-8")

    errors = runner._check_inventory(runner._compose_variables(), path)

    assert any("row IDs must be unique" in error for error in errors)
    assert any("oci_digest must be a sha256 digest" in error for error in errors)
    assert any("lock_hash must be a 64-hex hash" in error for error in errors)


def test_static_inventory_rejects_missing_local_build_recipe(tmp_path: Path) -> None:
    runner = _load_runner()
    inventory = yaml.safe_load(runner.IMAGE_INVENTORY_PATH.read_text(encoding="utf-8"))
    inventory["images"][0]["build_recipe"] = "infra/docker/Dockerfile.missing"
    path = tmp_path / "production_images.yaml"
    path.write_text(yaml.safe_dump(inventory, sort_keys=False), encoding="utf-8")

    errors = runner._check_inventory(runner._compose_variables(), path)

    assert any("build_recipe does not identify a checked-in file" in error for error in errors)

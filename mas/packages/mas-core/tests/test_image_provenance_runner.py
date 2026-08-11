"""Contract tests for static and live production-image evidence."""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "check_image_provenance.py"


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

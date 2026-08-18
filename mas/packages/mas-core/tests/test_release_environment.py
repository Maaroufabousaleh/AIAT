"""Tests for the secret-safe release environment manifest."""

from __future__ import annotations

import importlib.util
import json
import os
import re
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "check_release_environment.py"


def _load_checker():
    spec = importlib.util.spec_from_file_location("aiat_release_environment", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run_manifest(*extra: str) -> tuple[subprocess.CompletedProcess[str], dict[str, object]]:
    environment = os.environ.copy()
    environment["AIAT_API_KEY"] = "secret-test-value"
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--json", *extra],
        cwd=SCRIPT.parents[1],
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    return result, json.loads(result.stdout)


def test_release_environment_manifest_is_secret_safe_and_deterministic() -> None:
    first_result, first = _run_manifest()
    second_result, second = _run_manifest()

    assert first_result.returncode == 0, first_result.stderr
    assert second_result.returncode == 0, second_result.stderr
    assert first["schema_version"] == "aiat.release-environment.v1"
    assert first["status"] == "pass"
    assert first["manifest_digest"] == second["manifest_digest"]
    assert re.fullmatch(r"[0-9a-f]{64}", str(first["manifest_digest"]))
    assert first["git"]["revision"]
    assert len(first["tracked_inputs"]) == 14
    assert "docs/provenance/operator_pins.yaml" in {
        str(item["path"]) for item in first["tracked_inputs"]
    }
    assert "docs/provenance/security_scan_evidence.yaml" in {
        str(item["path"]) for item in first["tracked_inputs"]
    }
    assert "docs/provenance/security_scan_review.yaml" in {
        str(item["path"]) for item in first["tracked_inputs"]
    }
    assert "secret-test-value" not in json.dumps(first)
    assert all("value" not in item for item in first["environment_presence"])


def test_release_environment_manifest_reports_dirty_worktree_without_failing_default() -> None:
    result, report = _run_manifest()
    assert result.returncode == 0
    assert isinstance(report["git"]["working_tree_clean"], bool)
    if report["git"]["working_tree_clean"]:
        assert report["errors"] == []
    else:
        assert "working tree is dirty" not in report["errors"]


def test_native_release_preflight_is_secret_safe_and_fail_closed(monkeypatch) -> None:
    checker = _load_checker()
    monkeypatch.setattr(checker.platform, "system", lambda: "Linux")
    monkeypatch.setattr(checker.platform, "release", lambda: "6.8.0-microsoft-standard-WSL2")
    monkeypatch.setattr(checker.platform, "platform", lambda aliased=True: "test-wsl")
    monkeypatch.setattr(checker, "_docker_probe", lambda command: (False, "Docker Engine is unavailable"))
    for name in checker.PRODUCTION_IMAGE_ENV_INPUTS:
        monkeypatch.delenv(name, raising=False)

    report = checker.build_native_release_report(
        git={"working_tree_clean": True, "revision": "abc123"}
    )

    assert report["status"] == "blocked"
    assert "host is not a native Linux release host" in report["blockers"]
    assert report["docker"]["runsc_registered"] is False
    rendered = json.dumps(report)
    assert "secret-test-value" not in rendered
    assert all("value" not in row for row in report["image_refs"])


def test_native_release_preflight_passes_only_with_all_prerequisites(monkeypatch) -> None:
    checker = _load_checker()
    monkeypatch.setattr(checker.platform, "system", lambda: "Linux")
    monkeypatch.setattr(checker.platform, "release", lambda: "6.8.0-native")
    monkeypatch.setattr(checker.platform, "platform", lambda aliased=True: "test-native")
    monkeypatch.setattr(checker, "_docker_probe", lambda command: (True, None))
    monkeypatch.setattr(
        checker.subprocess,
        "run",
        lambda *args, **kwargs: type(
            "Result",
            (),
            {"returncode": 0, "stdout": '{"runsc": {}, "runc": {}}', "stderr": ""},
        )(),
    )
    for name in checker.PRODUCTION_IMAGE_ENV_INPUTS:
        monkeypatch.setenv(name, f"registry.example/{name.lower()}@sha256:{'a' * 64}")

    report = checker.build_native_release_report(
        git={"working_tree_clean": True, "revision": "abc123"}
    )

    assert report["status"] == "pass"
    assert report["docker"]["runsc_registered"] is True
    assert report["blockers"] == []

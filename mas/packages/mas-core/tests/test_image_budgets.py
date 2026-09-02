"""Tests for the bounded tool-service image budget contract."""

from __future__ import annotations

import importlib.util
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "check_image_budgets.py"


def _load_runner():
    spec = importlib.util.spec_from_file_location("check_image_budgets", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_static_contract_and_measurements_pass() -> None:
    runner = _load_runner()
    report, exit_code = runner.evaluate(
        budget_name="tool-service-core",
        image_ref="mas/tool-service:dev",
        compressed_bytes=700_000_000,
        startup_seconds=12.5,
        memory_bytes=100_000_000,
        size_reader=lambda _ref: 1_000_000_000,
    )
    assert exit_code == 0
    assert report["status"] == "pass"
    assert report["measurements"]["uncompressed_bytes"] == 1_000_000_000


def test_over_budget_measurement_fails() -> None:
    runner = _load_runner()
    report, exit_code = runner.evaluate(
        budget_name="tool-service-core",
        image_ref="mas/tool-service:dev",
        startup_seconds=31,
        size_reader=lambda _ref: 1,
    )
    assert exit_code == 1
    assert report["status"] == "fail"
    assert any("startup_seconds" in error for error in report["errors"])


def test_missing_local_image_is_blocked() -> None:
    runner = _load_runner()

    def unavailable(_ref):
        raise RuntimeError("docker CLI is unavailable")

    report, exit_code = runner.evaluate(image_ref="mas/tool-service:dev", size_reader=unavailable)
    assert exit_code == 2
    assert report["status"] == "blocked"

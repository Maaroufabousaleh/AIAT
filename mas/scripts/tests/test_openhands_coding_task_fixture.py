"""Offline tests for the real coding-task fixture."""

from __future__ import annotations

import importlib.util
from pathlib import Path


def _module():
    script = Path(__file__).resolve().parents[1] / "check_openhands_coding_task_fixture.py"
    spec = importlib.util.spec_from_file_location("check_openhands_coding_task_fixture", script)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_fixture_starts_incomplete_and_reference_change_passes() -> None:
    report = _module().exercise_fixture()
    assert report["status"] == "PASS"
    assert report["initial_tests_failed_as_expected"] is True
    assert report["reference_tests_passed"] is True
    assert report["changed_paths"] == ["slugger/core.py"]
    assert report["raw_test_output_retained"] is False

"""Validate the deterministic disposable OpenHands coding-task fixture."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

SCHEMA = "aiat.openhands-certification-coding-task-fixture-check.v1"
REFERENCE_IMPLEMENTATION = '''"""Slug formatting behavior required by the certification task."""

import re
import unicodedata


def slugify(value: str) -> str:
    """Return a lowercase, hyphen-separated ASCII slug."""

    normalized = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"-+", "-", re.sub(r"[^a-z0-9]+", "-", normalized.lower())).strip("-")
'''


def fixture_root() -> Path:
    return Path(__file__).resolve().parent / "fixtures" / "openhands-coding-task"


def validate_fixture(root: Path | None = None) -> dict[str, object]:
    root = root or fixture_root()
    required = (root / "pyproject.toml", root / "slugger" / "core.py", root / "tests" / "test_slugger.py")
    missing = [str(path.relative_to(root)) for path in required if not path.is_file()]
    return {
        "schema_version": SCHEMA,
        "fixture": "mas/scripts/fixtures/openhands-coding-task",
        "status": "PASS" if not missing else "FAILED_CERTIFICATION_IMPLEMENTATION",
        "missing_paths": missing,
        "network_required": False,
        "credentials_required": False,
        "payloads_retained": False,
    }


def exercise_fixture(root: Path | None = None) -> dict[str, object]:
    root = root or fixture_root()
    validation = validate_fixture(root)
    if validation["status"] != "PASS":
        return validation
    with tempfile.TemporaryDirectory(prefix="aiat-openhands-task-") as temporary:
        copy = Path(temporary) / "task"
        shutil.copytree(root, copy)
        before = subprocess.run(
            [sys.executable, "-m", "pytest", "-q"], cwd=copy, capture_output=True, text=True, check=False
        )
        initial_failed_as_expected = before.returncode != 0
        (copy / "slugger" / "core.py").write_text(REFERENCE_IMPLEMENTATION, encoding="utf-8")
        after = subprocess.run(
            [sys.executable, "-m", "pytest", "-q"], cwd=copy, capture_output=True, text=True, check=False
        )
        changed = sorted(
            str(path.relative_to(copy))
            for path in copy.rglob("*")
            if path.is_file()
            and "__pycache__" not in path.parts
            and path.suffix != ".pyc"
            and (root / path.relative_to(copy)).is_file()
            and path.read_bytes() != (root / path.relative_to(copy)).read_bytes()
        )
        return {
            **validation,
            "status": "PASS" if initial_failed_as_expected and after.returncode == 0 and changed == ["slugger/core.py"] else "FAILED_CERTIFICATION_IMPLEMENTATION",
            "initial_tests_failed_as_expected": initial_failed_as_expected,
            "reference_tests_passed": after.returncode == 0,
            "changed_paths": changed,
            "raw_test_output_retained": False,
        }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    report = exercise_fixture()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": report["status"], "changed_paths": report.get("changed_paths", [])}, sort_keys=True))
    return 0 if report["status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())

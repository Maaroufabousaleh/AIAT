"""Regression tests for scanner interpreter isolation in certification CI."""

from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]


def test_candidate_scanner_workflows_provision_tools_under_python_312() -> None:
    for workflow in (
        REPOSITORY_ROOT / ".github" / "workflows" / "opencode-candidate-certification.yml",
        REPOSITORY_ROOT / ".github" / "workflows" / "openhands-candidate-certification.yml",
    ):
        content = workflow.read_text(encoding="utf-8")
        assert "name: Set up scanner Python runtime" in content
        assert 'python-version: "3.12"' in content
        assert "uses: actions/checkout@11bd71901bbe5b1630ceea73d27597364c9af683" in content
        assert "uses: actions/setup-python@a26af69be951a213d495a4c3e4e4022e16d87065" in content
        assert "runs-on: ubuntu-24.04" in content
        assert "@v4" not in content
        assert "@v5" not in content

    openhands = (REPOSITORY_ROOT / ".github" / "workflows" / "openhands-candidate-certification.yml").read_text(
        encoding="utf-8"
    )
    assert "python3 scripts/provision_openhands_certification_tools.py" in openhands
    assert "--python python3" in openhands
    assert "uv run python scripts/provision_openhands_certification_tools.py" not in openhands

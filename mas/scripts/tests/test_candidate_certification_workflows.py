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
        assert "uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1" in content
        assert "uses: actions/setup-python@5fda3b95a4ea91299a34e894583c3862153e4b97" in content
        assert "uses: actions/upload-artifact@043fb46d1a93c77aae656e7c1c64a875d1fc6a0a" in content
        assert "runs-on: ubuntu-24.04" in content
        assert "@v4" not in content
        assert "@v5" not in content

    openhands = (REPOSITORY_ROOT / ".github" / "workflows" / "openhands-candidate-certification.yml").read_text(
        encoding="utf-8"
    )
    assert "python3 scripts/provision_openhands_certification_tools.py" in openhands
    assert "--python python3" in openhands
    assert "uv run python scripts/provision_openhands_certification_tools.py" not in openhands

def test_first_party_worker_evidence_workflow_is_pinned_and_non_deploying() -> None:
    workflow = (
        REPOSITORY_ROOT
        / ".github"
        / "workflows"
        / "first-party-worker-candidate-evidence.yml"
    ).read_text(encoding="utf-8")

    assert "runs-on: ubuntu-24.04" in workflow
    assert "test -z \"$(git status --porcelain)\"" in workflow
    assert "test \"$(uname -s)\" = Linux" in workflow
    assert "scripts/scan_first_party_worker_source.py" in workflow
    assert "test \"$ACTUAL_DIGEST\" = \"$expected\"" in workflow
    assert "scripts/check_native_gvisor_certification.py" in workflow
    assert "name: Set up scanner Python runtime" in workflow
    assert 'python-version: "3.12"' in workflow
    assert "Prepare deterministic worker dependency context" in workflow
    assert "Create pinned OCI-capable BuildKit builder" in workflow
    assert "--driver docker-container" in workflow
    assert 'features["containerd-snapshotter"] = True' in workflow
    assert "io.containerd.snapshotter.v1" in workflow
    assert "moby/buildkit:v0.34.2@sha256:c5f8862f52499773af85bc07695b2379ef08e8002352b0df2c4864c91d78dabe" in workflow
    assert "--builder aiat-worker-candidate" in workflow
    assert "Capture deterministic worker BuildKit failure detail" in workflow
    assert "id: provenance" in workflow
    assert "id: sbom" in workflow
    assert "provenance.sigstore.json" in workflow
    assert "scan-ref: ${{ runner.temp }}/aiat-worker-evidence/dependency-context" in workflow
    assert "Verify worker archive provenance and SBOM attestations" in workflow
    assert "gh attestation verify \"$archive\"" in workflow
    assert "--predicate-type https://cyclonedx.org/bom" in workflow
    assert "id: image_scan" in workflow
    assert "id: dependency_scan" in workflow
    assert "Enforce worker vulnerability scan results" in workflow
    assert "steps.image_scan.outcome" in workflow
    assert "steps.dependency_scan.outcome" in workflow
    assert "Evaluate refreshed Python 3.11 base candidate" in workflow
    assert "python:3.11-slim@sha256:07d4824036af339fa1b6ef769cc99d9c10a141769538b52b3b597c84f7a7b535" in workflow
    assert "refreshed-python-base-scan.json" in workflow
    assert "push-to-registry: false" in workflow
    assert "create-storage-record: false" in workflow
    assert "Remove disposable candidate builder" in workflow
    assert "CANDIDATE EVIDENCE ONLY" in workflow
    assert "branches:\n      - agent/fix-review-p1" in workflow
    assert "inputs.candidate_sha || github.sha" in workflow


def test_redis_evidence_workflow_is_scoped_to_reviewed_branch_pushes() -> None:
    workflow = (
        REPOSITORY_ROOT / ".github" / "workflows" / "redis-initializer-evidence.yml"
    ).read_text(encoding="utf-8")

    assert "branches:\n      - agent/fix-review-p1" in workflow
    assert "inputs.candidate_sha || github.sha" in workflow
    assert "PREPARATORY EVIDENCE ONLY" in workflow
    assert "Prepare Redis initializer source dependency context" in workflow
    assert "id: provenance" in workflow
    assert "id: sbom" in workflow
    assert "provenance.sigstore.json" in workflow
    assert "Verify Redis archive provenance and SBOM attestations" in workflow
    assert "gh attestation verify \"$archive\"" in workflow
    assert "--predicate-type https://cyclonedx.org/bom" in workflow
    assert "Enforce Redis security scan results" in workflow
    assert "scan-ref: ${{ runner.temp }}/aiat-redis-evidence/source-dependency-context" in workflow

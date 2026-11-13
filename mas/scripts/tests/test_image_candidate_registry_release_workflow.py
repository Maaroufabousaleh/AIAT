from __future__ import annotations

from pathlib import Path

import yaml

WORKFLOW = Path(__file__).resolve().parents[3] / ".github" / "workflows" / "image-candidate-registry-release.yml"


def test_candidate_publication_is_manual_and_defaults_to_no_publish() -> None:
    workflow = yaml.load(WORKFLOW.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)

    assert list(workflow["on"]) == ["workflow_dispatch"]
    inputs = workflow["on"]["workflow_dispatch"]["inputs"]
    assert inputs["publish_candidate"]["type"] == "boolean"
    assert inputs["publish_candidate"]["default"] == "false"
    assert "required" in inputs["candidate_sha"]


def test_package_write_is_confined_to_the_exact_approved_release_job() -> None:
    workflow = yaml.load(WORKFLOW.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
    preflight = workflow["jobs"]["preflight"]
    publish = workflow["jobs"]["publish"]

    assert "packages" not in preflight["permissions"]
    assert publish["permissions"]["packages"] == "write"
    assert "preflight" in publish["needs"]
    assert "inputs.publish_candidate" in publish["if"]
    assert publish["environment"] == "aiat-image-candidate-release"

    authorization = next(step for step in preflight["steps"] if step.get("name") == "Enforce candidate branch and publication authorization")
    assert "APPROVED_SHA" in authorization["env"]
    assert "CANDIDATE_SHA" in authorization["run"]
    assert "PROTECTED_ENVIRONMENT_READY" in authorization["env"]


def test_registry_publication_rechecks_scans_payload_and_registry_attestations() -> None:
    workflow = yaml.load(WORKFLOW.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
    publish = workflow["jobs"]["publish"]
    steps = publish["steps"]
    names = [step.get("name", "") for step in steps]

    assert names.index("Re-scan worker before publication") < names.index("Log in to GHCR after evidence and rescan gates pass")
    assert names.index("Re-scan Redis before publication") < names.index("Log in to GHCR after evidence and rescan gates pass")
    assert "Pull and compare the published worker image payload" in names
    assert "Pull and compare the published Redis image payload" in names
    assert "Verify registry-bound attestations" in names
    assert "Verify private package visibility and repository linkage" in names

    action_refs = [step["uses"] for step in steps if "uses" in step]
    for ref in action_refs:
        action, revision = ref.split("@", 1)
        assert len(revision.split()[0]) == 40, (action, revision)

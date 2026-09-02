from pathlib import Path

import pytest
import yaml

from mas_core.company_manifest import CompanyManifestError, compile_company_manifest


def _default_manifest() -> dict:
    path = Path(__file__).parents[3] / "companies" / "default-software-company.yaml"
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def test_default_company_manifest_compiles_with_stable_digest() -> None:
    manifest, digest, canonical = compile_company_manifest(_default_manifest())

    assert manifest.slug == "aiat-default"
    assert len(manifest.departments) == 11
    assert len(manifest.worker_assignments) == 11
    assert len(digest) == 64
    assert digest == manifest.digest()
    assert canonical["schema_version"] == "1"
    assert manifest.timezone == "UTC"
    assert manifest.retention is not None
    assert manifest.privacy is not None
    assert manifest.evidence_policy is not None
    assert manifest.evidence_policy.default_policy.policy_id == "software_delivery"
    assert manifest.deployment is not None
    assert manifest.deployment.sandbox_profile == "gvisor"


def test_company_manifest_rejects_unknown_worker_department() -> None:
    raw = _default_manifest()
    raw["worker_assignments"][0]["department_id"] = "unknown"

    with pytest.raises(CompanyManifestError, match="assigned to"):
        compile_company_manifest(raw)


def test_company_manifest_rejects_duplicate_worker_assignment() -> None:
    raw = _default_manifest()
    raw["worker_assignments"].append(dict(raw["worker_assignments"][0]))

    with pytest.raises(CompanyManifestError, match="assignments must be unique"):
        compile_company_manifest(raw)


@pytest.mark.parametrize("value", [-1.0, float("inf"), float("nan")])
def test_company_manifest_rejects_invalid_company_budget(value: float) -> None:
    raw = _default_manifest()
    raw["budgets"]["max_cost_usd"] = value

    with pytest.raises(CompanyManifestError, match="budgets"):
        compile_company_manifest(raw)


def test_company_manifest_rejects_chief_assigned_to_another_department() -> None:
    raw = _default_manifest()
    raw["worker_assignments"][1]["department_id"] = "exec_ceo"

    with pytest.raises(CompanyManifestError, match="chief 'coo'.*assigned to 'exec_ceo'"):
        compile_company_manifest(raw)


def test_company_manifest_rejects_duplicate_policy_entries() -> None:
    raw = _default_manifest()
    raw["privacy"]["external_worker_allowed_classes"] = ["internal", "internal"]

    with pytest.raises(CompanyManifestError, match="privacy classes must be unique"):
        compile_company_manifest(raw)


def test_company_manifest_rejects_model_default_outside_allowlist() -> None:
    raw = _default_manifest()
    raw["model_policy"]["allowed_profile_ids"] = ["safe-default"]
    raw["model_policy"]["default_profile_id"] = "other-profile"

    with pytest.raises(CompanyManifestError, match="default_profile_id"):
        compile_company_manifest(raw)


def test_legacy_company_manifest_without_policy_fields_remains_valid() -> None:
    raw = _default_manifest()
    for field in ("timezone", "retention", "privacy", "evidence_policy", "model_policy", "deployment"):
        raw.pop(field)

    manifest, _digest, _canonical = compile_company_manifest(raw)

    assert manifest.timezone == "UTC"
    assert manifest.retention is None
    assert manifest.deployment is None


def test_company_timezone_must_be_a_valid_iana_zone() -> None:
    raw = _default_manifest()
    raw["timezone"] = "Not/AZone"

    with pytest.raises(ValueError, match="valid IANA zone"):
        compile_company_manifest(raw)


def test_company_manifest_persists_milestone_evidence_policy_overrides() -> None:
    raw = _default_manifest()
    raw["evidence_policy"]["milestone_policies"] = {
        "implementation": {
            "policy_id": "operations",
            "version": "1.0",
            "requirements": {"required_artifact_kinds": ["deployment"]},
        }
    }

    manifest, _digest, canonical = compile_company_manifest(raw)

    assert manifest.evidence_policy is not None
    assert manifest.evidence_policy.milestone_policies["implementation"].policy_id == "operations"
    assert canonical["evidence_policy"]["milestone_policies"]["implementation"]["requirements"] == {
        "required_artifact_kinds": ["deployment"]
    }

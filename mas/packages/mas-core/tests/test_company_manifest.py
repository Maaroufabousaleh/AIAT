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

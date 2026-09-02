from __future__ import annotations

import importlib.util
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "check_optional_memory_services.py"
CATALOGUE = SCRIPT.parents[1] / "docs" / "provenance" / "optional_memory_services.yaml"


def _module():
    spec = importlib.util.spec_from_file_location("check_optional_memory_services", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_static_contract_is_pass_and_payload_free() -> None:
    module = _module()

    report = module.build_report(catalogue_path=CATALOGUE)

    assert report["schema_version"] == "aiat.optional-memory-services-check.v1"
    assert report["status"] == "pass"
    assert report["service_count"] == 3
    assert report["enabled_service_count"] == 0
    assert report["exact_adapter_contract_count"] == 3
    assert [row["id"] for row in report["services"]] == ["letta", "qdrant", "temporal"]
    assert [row["adapter_contract"] for row in report["services"]] == ["exact", "exact", "exact"]
    assert all(row["authority_owner"] == "aiat" for row in report["services"])
    assert all(row["raw_payload_retention"] == "forbidden" for row in report["services"])
    assert report["mutation_performed"] is False
    assert report["network_access_performed"] is False
    assert report["licence_metadata_is_gate"] is False


def test_live_contract_is_blocked_without_contacting_optional_services() -> None:
    module = _module()

    report = module.build_report(catalogue_path=CATALOGUE, live=True)

    assert report["status"] == "blocked"
    assert report["mutation_performed"] is False
    assert report["network_access_performed"] is False
    assert "no live service was contacted" in report["reason"]


def test_invalid_catalogue_remains_a_contract_failure_in_live_mode(tmp_path: Path) -> None:
    module = _module()
    invalid = tmp_path / "invalid.yaml"
    invalid.write_text(
        "schema_version: aiat.optional-memory-services.v1\n"
        "programme_scope: personal-internal-only\n"
        "policy: {}\n"
        "services: []\n",
        encoding="utf-8",
    )

    report = module.build_report(catalogue_path=invalid, live=True)

    assert report["status"] == "fail"
    assert report["errors"]
    assert report["mutation_performed"] is False
    assert report["network_access_performed"] is False


def test_qdrant_and_temporal_require_concrete_adapter_contracts(tmp_path: Path) -> None:
    module = _module()
    catalogue = module.yaml.safe_load(CATALOGUE.read_text(encoding="utf-8"))
    catalogue["services"][1]["adapter"] = "external_adapter_pending_exact_contract"
    invalid = tmp_path / "invalid-adapter.yaml"
    invalid.write_text(module.yaml.safe_dump(catalogue, sort_keys=False), encoding="utf-8")

    report = module.build_report(catalogue_path=invalid)

    assert report["status"] == "fail"
    assert any("services[1].adapter must be" in error for error in report["errors"])
    assert report["mutation_performed"] is False
    assert report["network_access_performed"] is False

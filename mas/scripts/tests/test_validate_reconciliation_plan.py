"""Preservation-only plans must never silently authorize imports."""

import importlib.util
from pathlib import Path

import pytest

_path = Path(__file__).resolve().parents[1] / "validate_reconciliation_plan.py"
_spec = importlib.util.spec_from_file_location("validate_reconciliation_plan", _path)
_module = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_module)


def plan():
    return {
        "schema_version": 1, "mode": "read_only_plan_no_import",
        "active_database_imports_authorized": [],
        "foreign_key_graphs": {"desktop_copy": [{}], "native_copy": [{}]},
        "records": [{"table": "projects", "key_hash": "a" * 32,
                     "situation": "same_key_conflict", "classification": "operator_security_decision",
                     "action": "preserve_and_block_import", "automatic_write": False}],
    }


def test_preservation_plan_does_not_certify_closure():
    result = _module.validate(plan())
    assert result["valid"]
    assert not result["activation_authorized"]
    assert not result["referential_closure_verified"]


@pytest.mark.parametrize("mutation", ["authorized", "automatic", "conflict", "duplicate", "missing_graph", "invalid_type"])
def test_unsafe_or_incomplete_plan_is_rejected(mutation):
    candidate = plan()
    if mutation == "authorized":
        candidate["active_database_imports_authorized"] = ["projects"]
    elif mutation == "automatic":
        candidate["records"][0]["automatic_write"] = True
    elif mutation == "conflict":
        candidate["records"][0]["classification"] = "historical_archive_candidate"
        candidate["records"][0]["action"] = "preserve_read_only_pending_integrity_review"
    elif mutation == "duplicate":
        candidate["records"].append(dict(candidate["records"][0]))
    elif mutation == "missing_graph":
        candidate["foreign_key_graphs"]["desktop_copy"] = []
    else:
        candidate["records"][0]["situation"] = []
    assert not _module.validate(candidate)["valid"]

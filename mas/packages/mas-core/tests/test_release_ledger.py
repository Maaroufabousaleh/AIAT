"""Tests for the machine-readable release-evidence aggregator."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "check_release_ledger.py"


def test_static_release_ledger_aggregates_bounded_verifiers_without_release_claim() -> None:
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--json"],
        cwd=SCRIPT.parents[1],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["schema_version"] == "aiat.release-ledger.v1"
    assert report["profile"] == "static"
    assert report["status"] == "pass"
    assert report["release_decision"] == "NO-RELEASE"
    assert report["counts"]["total"] >= 15
    assert report["policy"] == {
        "blocked_live_evidence_is_pass": False,
        "licence_metadata_is_gate": False,
        "programme_scope": "personal-internal-only",
    }
    worker = next(row for row in report["checks"] if row["id"] == "worker_reconciliation")
    assert worker["pending_evidence_count"] >= 1
    bindings = next(row for row in report["checks"] if row["id"] == "default_worker_bindings")
    assert bindings["status"] == "pass"
    assert bindings["summary"] == {"errors": [], "status": "pass"}
    firecracker = next(
        row for row in report["checks"] if row["id"] == "firecracker_worker_pool_readiness"
    )
    assert firecracker["category"] == "integration"
    assert firecracker["status"] == "pass"
    assert firecracker["summary"] == {"status": "pass", "mode": "static"}
    provider_recovery = next(
        row for row in report["checks"] if row["id"] == "gateway_provider_recovery"
    )
    assert provider_recovery["category"] == "recovery"
    assert provider_recovery["status"] == "pass"
    assert provider_recovery["summary"] == {
        "external_provider_call_performed": False,
        "mode": "fixture",
        "status": "pass",
    }
    security_review = next(
        row for row in report["checks"] if row["id"] == "security_scan_review"
    )
    assert security_review["category"] == "security"
    assert security_review["status"] == "pass"
    assert security_review["summary"] == {
        "errors": [],
        "engine_warning_count": 54,
        "finding_count": 316,
        "mode": "static",
        "review_required_count": 1,
        "scan_count": 1,
        "status": "pass",
        "technical_gate_status": "blocked",
    }
    evidence_resolution = next(row for row in report["checks"] if row["id"] == "evidence_policy_resolution")
    assert evidence_resolution["status"] == "pass"
    assert evidence_resolution["summary"]["errors"] == []
    assert evidence_resolution["summary"]["mode"] == "fixture"
    assert evidence_resolution["summary"]["status"] == "pass"
    assert evidence_resolution["summary"]["case_count"] == 7
    external_policy = next(row for row in report["checks"] if row["id"] == "external_account_action_policy")
    assert external_policy["status"] == "pass"
    assert external_policy["summary"] == {
        "errors": [],
        "mode": "fixture",
        "status": "pass",
    }
    adapter_declarations = next(
        row for row in report["checks"] if row["id"] == "provider_adapter_declarations"
    )
    assert adapter_declarations["status"] == "pass"
    assert adapter_declarations["summary"] == {
        "errors": [],
        "mode": "fixture",
        "status": "pass",
    }
    http_conformance = next(
        row for row in report["checks"] if row["id"] == "provider_adapter_http_conformance"
    )
    assert http_conformance["status"] == "pass"
    assert http_conformance["summary"]["errors"] == []
    assert http_conformance["summary"]["mode"] == "mocked_fixture"
    assert http_conformance["summary"]["status"] == "pass"
    assert http_conformance["summary"]["case_count"] == 8
    assert http_conformance["summary"]["passed_case_count"] == 8
    identity_conformance = next(
        row for row in report["checks"] if row["id"] == "identity_provider_conformance"
    )
    assert identity_conformance["category"] == "contract"
    assert identity_conformance["status"] == "pass"
    assert identity_conformance["summary"]["case_count"] == 11
    assert identity_conformance["summary"]["passed_case_count"] == 11
    assert identity_conformance["summary"]["error_count"] == 0
    assert identity_conformance["summary"]["external_network_access_performed"] is False
    assert identity_conformance["summary"]["external_provider_mutation_performed"] is False
    assert identity_conformance["summary"]["payload_free"] is True
    lifecycle = next(row for row in report["checks"] if row["id"] == "external_account_lifecycle")
    assert lifecycle["status"] == "pass"
    assert lifecycle["summary"]["errors"] == []
    assert lifecycle["summary"]["mode"] == "fixture"
    assert lifecycle["summary"]["status"] == "pass"
    assert lifecycle["summary"]["case_count"] == 8
    assert lifecycle["summary"]["passed_case_count"] == 8
    outbound = next(row for row in report["checks"] if row["id"] == "outbound_mail_lifecycle")
    assert outbound["status"] == "pass"
    assert outbound["summary"]["errors"] == []
    assert outbound["summary"]["mode"] == "fixture"
    assert outbound["summary"]["status"] == "pass"
    assert outbound["summary"]["case_count"] == 6
    assert outbound["summary"]["passed_case_count"] == 6
    lifecycle = next(row for row in report["checks"] if row["id"] == "worker_run_lifecycle")
    assert lifecycle["status"] == "pass"
    assert lifecycle["summary"]["mode"] == "fixture"
    assert lifecycle["summary"]["status"] == "pass"
    assert lifecycle["summary"]["certification_boundary"] == {
        "artifact_before_terminal": "checked",
        "canary": "not_checked",
        "checkpoint_persistence": "checked",
        "cold_cancellation": "checked",
        "cold_crash": "checked",
        "controller_contract": "checked",
        "database": "not_checked",
        "lease_expiry_recovery": "checked",
        "live_worker_run": "not_checked",
        "pause_resume": "checked",
        "rollback": "not_checked",
        "sandbox": "not_checked",
    }
    candidates = next(
        row for row in report["checks"] if row["id"] == "self_improvement_candidate_detection"
    )
    assert candidates["status"] == "pass"
    assert candidates["summary"]["errors"] == []
    assert candidates["summary"]["mode"] == "fixture"
    assert candidates["summary"]["status"] == "pass"
    assert candidates["summary"]["case_count"] == 6
    assert candidates["summary"]["passed_case_count"] == 6
    flow_semantics = next(row for row in report["checks"] if row["id"] == "flow_execution_semantics")
    assert flow_semantics["status"] == "pass"
    flow_binding = next(row for row in report["checks"] if row["id"] == "flow_worker_binding")
    assert flow_binding["status"] == "pass"
    watchdog = next(row for row in report["checks"] if row["id"] == "workflow_watchdog_recovery")
    assert watchdog["status"] == "pass"
    metric = next(row for row in report["checks"] if row["id"] == "metric_series_budget")
    assert metric["status"] == "pass"
    assert metric["summary"]["declared_label_inventory"]["mas_messages"] == [
        "direction",
        "msg_type",
        "team",
    ]
    assert metric["summary"]["label_inventory"]["mas_project_state"] == ["state"]
    assert (
        metric["summary"]["label_policies"]["mas_project_state"]["state"]["classification"]
        == "bounded"
    )
    docs = next(row for row in report["checks"] if row["id"] == "documentation_index")
    assert docs["status"] == "pass"
    multipart = next(row for row in report["checks"] if row["id"] == "object_store_multipart")
    assert multipart["category"] == "contract"
    assert multipart["status"] == "pass"
    assert multipart["summary"] == {
        "abort_verified": True,
        "cleanup_verified": True,
        "error_count": 0,
        "mode": "fixture",
        "status": "pass",
    }
    resource_profile = next(
        row for row in report["checks"] if row["id"] == "object_store_resource_profile"
    )
    assert resource_profile["category"] == "operations"
    assert resource_profile["status"] == "pass"
    assert resource_profile["summary"]["measurement_source"] in {"procfs", "resource"}
    assert resource_profile["summary"]["cleanup_verified"] is True
    assert resource_profile["summary"]["error_count"] == 0
    runtime_profile = next(row for row in report["checks"] if row["id"] == "runtime_install_profile")
    assert runtime_profile["status"] == "pass"
    assert runtime_profile["summary"]["locked_versions"] == {
        "crewai": "1.6.1",
        "langgraph": "0.6.11",
    }
    operator_pins = next(row for row in report["checks"] if row["id"] == "operator_pins")
    assert operator_pins["status"] == "pass"
    assert operator_pins["summary"]["pin_count"] == 16
    assert operator_pins["summary"]["locked_count"] == 9
    assert operator_pins["summary"]["unavailable_count"] == 7
    release_environment = next(
        row for row in report["checks"] if row["id"] == "release_environment"
    )
    assert release_environment["status"] == "pass"
    assert "live profile was not included" in report["decision_reasons"]
    assert "NO-RELEASE" in result.stdout


def test_release_ledger_redacts_secret_shaped_diagnostics() -> None:
    from importlib.util import module_from_spec, spec_from_file_location

    spec = spec_from_file_location("check_release_ledger", SCRIPT)
    assert spec and spec.loader
    module = module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)

    payload = {
        "status": "fail",
        "live": {
            "status": "fail",
            "errors": ["unexpected password=do-not-print token=also-secret"],
            "containers": [{"forbidden_env": ["AIAT_API_KEY"]}],
        },
    }
    summary = module._safe_summary(payload, "", "")
    rendered = json.dumps(summary)
    assert "do-not-print" not in rendered
    assert "also-secret" not in rendered
    assert "container_count" in summary["live"]
    assert module._status_for(0, {"status": "pass", "live": {"status": "blocked"}}, live=True) == "blocked"

    native_summary = module._safe_summary(
        {
            "status": "blocked",
            "native_release": {
                "status": "blocked",
                "platform": {
                    "system": "Linux",
                    "kernel": "6.8.0-native",
                    "native_linux": True,
                    "description": "not retained",
                },
                "docker": {
                    "engine_available": True,
                    "compose_v2_available": True,
                    "runtimes_metadata_available": True,
                    "runsc_registered": False,
                },
                "image_refs": [
                    {
                        "name": "AIAT_IMAGE_REF",
                        "configured": True,
                        "digest_pinned": True,
                        "value": "registry.example/secret@sha256:abc",
                    }
                ],
                "blockers": ["gVisor runsc runtime is not registered"],
            },
        },
        "",
        "",
    )
    assert native_summary["native_release"]["status"] == "blocked"
    assert native_summary["native_release"]["docker"]["runsc_registered"] is False
    assert "registry.example/secret" not in json.dumps(native_summary)
    assert "value" not in json.dumps(native_summary)

    catalogue_summary = module._safe_summary(
        {
            "schema_version": "aiat.model-profile-catalogue.v1",
            "profile_version_count": 4,
            "covered_profile_version_count": 3,
            "profile_pending_model_count": 1,
            "findings": [{"code": "PROFILE_MODEL_NOT_REGISTERED"}],
        },
        "",
        "",
    )
    assert catalogue_summary["profile_version_count"] == 4
    assert catalogue_summary["finding_count"] == 1


def test_trace_evidence_live_check_uses_a_bounded_retained_trace_id() -> None:
    from importlib.util import module_from_spec, spec_from_file_location

    spec = spec_from_file_location("check_release_ledger_inventory", SCRIPT)
    assert spec and spec.loader
    module = module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)

    _inventory, checks = module._load_inventory()
    trace = next(row for row in checks if row.check_id == "trace_evidence")
    assert trace.live_args == (
        "--live",
        "--json",
        "--trace-id",
        "aiat-live-trace-20260811-phase",
    )


def test_release_environment_live_check_requires_native_release_prerequisites() -> None:
    from importlib.util import module_from_spec, spec_from_file_location

    spec = spec_from_file_location("check_release_ledger_native_preflight", SCRIPT)
    assert spec and spec.loader
    module = module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)

    _inventory, checks = module._load_inventory()
    release_environment = next(row for row in checks if row.check_id == "release_environment")
    assert release_environment.live_args == ("--require-native-linux", "--json")


def test_live_release_ledger_bounds_timed_out_checker(monkeypatch) -> None:
    from importlib.util import module_from_spec, spec_from_file_location

    spec = spec_from_file_location("check_release_ledger_timeout", SCRIPT)
    assert spec and spec.loader
    module = module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)

    def raise_timeout(*args, **kwargs):
        assert kwargs["timeout"] == module._DEFAULT_LIVE_CHECK_TIMEOUT_SECONDS
        raise subprocess.TimeoutExpired(cmd=args[0], timeout=kwargs["timeout"])

    monkeypatch.setattr(module.subprocess, "run", raise_timeout)
    row = module._run_check(
        module.CheckSpec(
            check_id="slow_live_probe",
            category="integration",
            script="scripts/check_live_trace_observability.py",
            args=(),
            live_args=("--live", "--json"),
        ),
        live=True,
    )

    assert row["status"] == "blocked"
    assert row["exit_code"] == 2
    assert row["pending_evidence_count"] == 0
    assert row["summary"] == {
        "status": "blocked",
        "reason": "checker timed out after 60s",
        "timeout_seconds": 60.0,
    }


def test_release_ledger_runs_independent_checks_in_inventory_order(monkeypatch) -> None:
    from importlib.util import module_from_spec, spec_from_file_location

    spec = spec_from_file_location("check_release_ledger_parallel", SCRIPT)
    assert spec and spec.loader
    module = module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)

    specs = [
        module.CheckSpec(check_id=name, category="static", script="scripts/noop.py", args=())
        for name in ("first", "second", "third")
    ]
    observed: list[str] = []

    def fake_run(check, *, live):
        assert live is False
        observed.append(check.check_id)
        return {"id": check.check_id, "status": "pass", "pending_evidence_count": 0}

    monkeypatch.setattr(module, "_check_worker_count", lambda: 2)
    monkeypatch.setattr(module, "_run_check", fake_run)

    rows = module._run_checks(specs, live=False)

    assert [row["id"] for row in rows] == ["first", "second", "third"]
    assert sorted(observed) == ["first", "second", "third"]

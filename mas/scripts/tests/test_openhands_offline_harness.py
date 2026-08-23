"""Offline lifecycle, isolation, and negative-security harness tests."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


def _module():
    script = Path(__file__).resolve().parents[1] / "openhands_offline_harness.py"
    spec = importlib.util.spec_from_file_location("openhands_offline_harness", script)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["openhands_offline_harness"] = module
    spec.loader.exec_module(module)
    return module


def test_lifecycle_state_machine_is_fail_closed_and_idempotent() -> None:
    module = _module()
    conversation = module.FakeConversation(max_units=1)
    conversation.start()
    assert conversation.pause() == "PAUSED"
    assert conversation.pause() == "IDEMPOTENT"
    conversation.resume()
    assert conversation.interrupt() == "INTERRUPTED"
    assert conversation.interrupt() == "IDEMPOTENT"
    conversation.resume()
    conversation.crash()
    conversation.recover()
    conversation.start()
    conversation.timeout()
    assert conversation.state == "TIMED_OUT"


def test_negative_security_and_secret_scan_retain_no_values() -> None:
    module = _module()
    assert module.workspace_access("/a/workspace/file", "/a/workspace") is True
    assert module.workspace_access("/a/workspace/../other", "/a/workspace") is False
    assert module.forbidden_tool_attempt("github.write")["denied"] is True
    assert module.forbidden_tool_attempt("terminal")["denied"] is False
    denial = module.model_override_denial({"prompt": "x", "model": "bad", "api_key": "secret"})
    assert denial["denied"] is True
    scan = module.scan_secret_disclosure(("secret-value",), ("safe evidence",))
    assert scan["status"] == "PASS"
    assert "secret-value" not in str(scan)
    generated = module.scan_secret_disclosure((value for value in ("generated-secret",)), ("safe evidence",))
    assert generated["status"] == "PASS"
    assert "generated-secret" not in str(generated)


def test_complete_offline_harness_is_fixture_only() -> None:
    report = _module().run_offline_harness()
    assert report["mode"] == "offline_fixture_only"
    assert report["coding_task"] == "PASS"
    assert report["pause"] == "PASS"
    assert report["interrupt"] == "PASS"
    assert report["resume"] == "PASS"
    assert report["ordinary_completion_not_resumable"] == "PASS"
    assert report["forced_failure"] == "PASS"
    assert report["recovery"] == "PASS"
    assert report["timeout"] == "PASS"
    assert report["budget"] == "PASS"
    assert report["forbidden_tool"] == "PASS"
    assert report["workspace_isolation"] == "PASS"
    assert report["secret_isolation"] == "PASS"
    assert report["zero_residue"]["status"] == "PASS"
    assert report["details"]["partial_startup_cleanup"]["status"] == "PASS"
    assert report["details"]["workspace_attacks"]["attempt_count"] == 6
    assert report["details"]["workspace_attacks"]["denied_count"] == 6
    assert report["details"]["workspace_attacks"]["symlink_traversal_tested"] is True
    assert report["details"]["lifecycle_races"]["status"] == "PASS"
    assert report["details"]["recovery_idempotency"] == "PASS"
    assert report["details"]["partial_startup_cleanup"]["scenarios"]["mcp_after_failed_task"]["started_resources"]
    assert report["mandatory_gate_count"] == 20
    assert report["live_certification_required"] is True
    assert len(report["fixture_gate_results"]) == 20
    assert report["fixture_gate_results"]["graceful_pause"] == "PASS"
    assert report["fixture_gate_results"]["test_execution"] == "NOT_RUN"
    assert report["fixture_gate_results"]["real_coding_task"] == "NOT_RUN"

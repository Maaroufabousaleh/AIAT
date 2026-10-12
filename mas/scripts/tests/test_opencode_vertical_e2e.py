from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from typing import Any

SCRIPT = Path(__file__).resolve().parents[1] / "opencode_vertical_e2e.py"
SPEC = importlib.util.spec_from_file_location("opencode_vertical_e2e", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
DRIVER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(DRIVER)


class _PendingWorkerAPI:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def request(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
    ) -> tuple[int, bytes, Any, dict[str, str]]:
        del payload
        self.calls.append((method, path))
        if method == "POST" and path == "/projects":
            return 201, b"", {"id": "disposable-project", "state": "PLANNING"}, {}
        if method == "GET" and path == "/capabilities/workers":
            return (
                200,
                b"",
                [
                    {
                        "id": "worker-opencode",
                        "status": "INACTIVE",
                        "adapter_type": "opencode",
                        "evaluation_status": "compatibility_pending_governance",
                    }
                ],
                {},
            )
        if method == "DELETE" and path == "/projects/disposable-project":
            return 204, b"", None, {}
        if method == "GET" and path == "/projects/disposable-project":
            raise DRIVER.APIError(404, "RESOURCE_NOT_FOUND")
        raise AssertionError(f"unexpected API call: {method} {path}")


def test_pending_worker_gate_still_cleans_disposable_project(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    api = _PendingWorkerAPI()
    monkeypatch.setattr(
        DRIVER,
        "_verify_local_fixture_environment",
        lambda: (api, "worker-opencode"),
    )
    evidence = tmp_path / "evidence.json"

    exit_code = DRIVER.main(["--evidence", str(evidence)])

    report = json.loads(evidence.read_text(encoding="utf-8"))
    stages = report["stages"]
    assert exit_code == 2
    assert stages["WORKER_SELECTED"]["status"] == "BLOCKED"
    assert stages["WORKER_RUN_SUBMITTED"]["status"] == "NOT_RUN"
    assert stages["CLEANUP_VERIFIED"]["status"] == "PASS"
    assert ("DELETE", "/projects/disposable-project") in api.calls
    assert not any(method == "POST" and path == "/workers/runs" for method, path in api.calls)


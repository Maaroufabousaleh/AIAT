"""Tests for governed asynchronous flow-task Worker Run bindings."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

from mas_core.memory.storage import AgentStorage
from mas_core.workflow.worker_binding import (
    bind_pending_worker_run,
    classify_worker_run_state,
    clear_worker_run_binding,
)

SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "check_flow_worker_binding.py"


def test_pending_binding_is_copy_on_write_and_parallel_safe() -> None:
    original = {"active_worker_runs": {"a": {"run_id": "run-a", "state": "RUNNING"}}}
    bound = bind_pending_worker_run(
        original,
        node_id="b",
        run_id="run-b",
        state="queued",
        dispatch_mode="queued",
    )
    assert set(bound["active_worker_runs"]) == {"a", "b"}
    assert original == {"active_worker_runs": {"a": {"run_id": "run-a", "state": "RUNNING"}}}
    settled = clear_worker_run_binding(bound, node_id="b")
    assert settled == original


def test_unknown_worker_run_state_is_not_bindable() -> None:
    assert classify_worker_run_state("future_state") == "unknown"
    try:
        bind_pending_worker_run({}, node_id="task", run_id="run", state="future_state")
    except ValueError as exc:
        assert "non-terminal" in str(exc)
    else:
        raise AssertionError("unknown state was accepted")


def test_retry_storage_marks_history_without_delete() -> None:
    class Connection:
        statement = None

        async def execute(self, statement):
            self.statement = statement
            return SimpleNamespace(rowcount=2)

    class Transaction:
        def __init__(self) -> None:
            self.connection = Connection()

        async def __aenter__(self):
            return self.connection

        async def __aexit__(self, *_args):
            return None

    class Engine:
        def __init__(self) -> None:
            self.transaction = Transaction()

        def begin(self):
            return self.transaction

    engine = Engine()
    storage = object.__new__(AgentStorage)
    storage._engine = engine

    import anyio

    async def invoke() -> int:
        return await storage.supersede_flow_node_executions(uuid4(), reason="retry fixture")

    count = anyio.run(invoke)

    assert count == 2
    rendered = str(engine.transaction.connection.statement)
    assert "UPDATE flow_node_executions" in rendered
    assert "DELETE FROM flow_node_executions" not in rendered


def test_checker_static_report_passes_and_live_is_blocked() -> None:
    static = subprocess.run(
        [sys.executable, str(SCRIPT), "--json"],
        check=True,
        capture_output=True,
        text=True,
    )
    report = json.loads(static.stdout)
    assert report["schema_version"] == "aiat.flow-worker-binding.v1"
    assert report["status"] == "pass"
    assert report["mutation"] == {"storage": False, "worker_dispatch": False}
    assert report["licence_metadata"]["affects_discovery_install_activation_or_execution"] is False

    module_spec = importlib.util.spec_from_file_location("check_flow_worker_binding", SCRIPT)
    assert module_spec and module_spec.loader
    module = importlib.util.module_from_spec(module_spec)
    module_spec.loader.exec_module(module)
    assert module.main(["--live", "--json"]) == 2

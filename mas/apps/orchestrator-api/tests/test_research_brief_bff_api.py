"""Hermetic BFF-to-API Research Brief task-input integration coverage.

The dashboard BFF route and orchestrator FastAPI route are real source code.
Only persistence and the WorkerRun result boundary are faked; no adapter or
external runtime is constructed or executed.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import socket
import subprocess
import threading
import time
from contextlib import suppress
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID, uuid4

import pytest
import uvicorn

API_KEY = "synthetic-research-brief-operator-key"
INSTANCE_ID = UUID("50000000-0000-4000-8000-000000000501")
FLOW_ID = UUID("50000000-0000-4000-8000-000000000502")
PROJECT_ID = UUID("50000000-0000-4000-8000-000000000503")
RESEARCH_WORKER_ID = UUID("50000000-0000-4000-8000-000000000504")
REPORT_WORKER_ID = UUID("50000000-0000-4000-8000-000000000505")
RESEARCH_CAPABILITY_ID = UUID("50000000-0000-4000-8000-000000000506")
REPORT_CAPABILITY_ID = UUID("50000000-0000-4000-8000-000000000507")


def _flow_definition() -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "metadata": {"template_id": "research"},
        "nodes": [
            {"id": "start", "type": "start", "label": "Start", "config": {}},
            {
                "id": "research",
                "type": "task",
                "label": "Research",
                "config": {
                    "worker_id": str(RESEARCH_WORKER_ID),
                    "model_mode": "none",
                    "task_type": "research.collect",
                    "task_input": {"query": "AIAT task input contract"},
                },
            },
            {
                "id": "report",
                "type": "task",
                "label": "Write report",
                "config": {
                    "worker_id": str(REPORT_WORKER_ID),
                    "model_mode": "none",
                    "task_type": "research.report",
                    # Omitted input deliberately uses the documented legacy
                    # flow_context/flow_node task contract.
                },
            },
            {"id": "end", "type": "end", "label": "Complete", "config": {}},
        ],
        "edges": [
            {"id": "start-research", "source": "start", "target": "research"},
            {"id": "research-report", "source": "research", "target": "report"},
            {"id": "report-end", "source": "report", "target": "end"},
        ],
    }


class _ResearchBriefStorage:
    def __init__(self) -> None:
        now = datetime.now(UTC)
        self.flow = {
            "id": FLOW_ID,
            "name": "Research Brief fixture",
            "description": "Hermetic task-input integration fixture",
            "version": 1,
            "is_active": True,
            "definition_json": _flow_definition(),
        }
        self.project = {
            "id": PROJECT_ID,
            "name": "Research Brief fixture project",
            "description": "Synthetic project state used only in an offline test",
            "state": "IN_PROGRESS",
            "company_id": None,
            "created_by": "test-operator",
            "created_at": now,
            "updated_at": now,
        }
        self.instance = {
            "id": INSTANCE_ID,
            "project_id": PROJECT_ID,
            "flow_id": FLOW_ID,
            "flow_version": 1,
            "status": "RUNNING",
            "active_node_ids": ["research"],
            "context_json": {"brief_topic": "AIAT task input contracts"},
            "started_at": now,
            "created_at": now,
            "updated_at": now,
            "retry_count": 0,
        }
        self.executions: list[dict[str, Any]] = [
            {
                "id": 1,
                "instance_id": INSTANCE_ID,
                "node_id": "research",
                "node_type": "task",
                "node_label": "Research",
                "status": "RUNNING",
                "input_json": deepcopy(self.instance["context_json"]),
                "output_json": None,
                "error": None,
                "retry_count": 0,
                "started_at": now,
                "completed_at": None,
            }
        ]
        self.workers = {
            RESEARCH_WORKER_ID: {
                "id": RESEARCH_WORKER_ID,
                "status": "ACTIVE",
                "model_mode": "none",
                "active_shell_version_id": uuid4(),
                "active_adapter_id": uuid4(),
                "adapter_config": {},
                "capability_ids": [RESEARCH_CAPABILITY_ID],
            },
            REPORT_WORKER_ID: {
                "id": REPORT_WORKER_ID,
                "status": "ACTIVE",
                "model_mode": "none",
                "active_shell_version_id": uuid4(),
                "active_adapter_id": uuid4(),
                "adapter_config": {},
                "capability_ids": [REPORT_CAPABILITY_ID],
            },
        }
        self.capabilities = [
            {
                "id": RESEARCH_CAPABILITY_ID,
                "name": "research.collect",
                "input_schema": {
                    "type": "object",
                    "properties": {"query": {"type": "string", "minLength": 1}},
                    "required": ["query"],
                    "additionalProperties": False,
                },
            },
            {
                "id": REPORT_CAPABILITY_ID,
                "name": "research.report",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "flow_context": {
                            "type": "object",
                            "properties": {
                                "research_summary": {"type": "string", "minLength": 1},
                                "sources": {
                                    "type": "array",
                                    "items": {
                                        "type": "object",
                                        "properties": {
                                            "evidence_id": {"type": "string", "minLength": 1},
                                            "label": {"type": "string", "minLength": 1},
                                        },
                                        "required": ["evidence_id", "label"],
                                        "additionalProperties": False,
                                    },
                                    "minItems": 1,
                                },
                            },
                            "required": ["research_summary", "sources"],
                            "additionalProperties": True,
                        },
                        "flow_node": {"type": "object"},
                    },
                    "required": ["flow_context", "flow_node"],
                    "additionalProperties": False,
                },
            },
        ]
        self.worker_runs: dict[UUID, dict[str, Any]] = {}
        self.admissions: list[dict[str, Any]] = []
        self._next_execution_id = 2

    async def get_project(self, project_id: UUID) -> dict[str, Any] | None:
        return deepcopy(self.project) if project_id == PROJECT_ID else None

    async def get_worker(self, worker_id: UUID) -> dict[str, Any] | None:
        worker = self.workers.get(worker_id)
        return deepcopy(worker) if worker else None

    async def list_capabilities(self) -> list[dict[str, Any]]:
        return deepcopy(self.capabilities)

    async def get_flow(self, flow_id: UUID) -> dict[str, Any] | None:
        return deepcopy(self.flow) if flow_id == FLOW_ID else None

    async def get_flow_instance(self, instance_id: UUID) -> dict[str, Any] | None:
        return deepcopy(self.instance) if instance_id == INSTANCE_ID else None

    async def list_flow_node_executions(
        self,
        *,
        instance_id: UUID,
        node_id: str | None = None,
        status: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        rows = [row for row in self.executions if row["instance_id"] == instance_id]
        if node_id is not None:
            rows = [row for row in rows if row["node_id"] == node_id]
        if status is not None:
            rows = [row for row in rows if row["status"] == status]
        return deepcopy(rows[offset : offset + limit])

    async def get_worker_run(self, run_id: UUID) -> dict[str, Any] | None:
        row = self.worker_runs.get(run_id)
        return deepcopy(row) if row else None

    async def create_model_resolution_snapshot(self, **kwargs: Any) -> dict[str, Any]:
        return {"snapshot": deepcopy(kwargs.get("snapshot"))}

    async def create_worker_run(self, **kwargs: Any) -> dict[str, Any]:
        row = {
            "id": kwargs["run_id"],
            "worker_id": kwargs["worker_id"],
            "project_id": kwargs["project_id"],
            "flow_id": kwargs["flow_id"],
            "flow_instance_id": kwargs["flow_instance_id"],
            "flow_node_execution_id": kwargs["flow_node_execution_id"],
            "task_type": kwargs["task_type"],
            "request_json": deepcopy(kwargs["request"]),
            "state": kwargs["state"],
            "result_json": None,
        }
        self.worker_runs[row["id"]] = row
        self.admissions.append(deepcopy(row))
        return deepcopy(row)

    async def claim_worker_run(self, **_kwargs: Any) -> None:
        # MAS_ENVIRONMENT=production selects the queued path. A missing claim
        # means the fixture stores admission but never executes an adapter.
        return None

    async def activate_flow_nodes(
        self,
        instance_id: UUID,
        *,
        expected_statuses: set[str],
        expected_active_node_ids: list[str],
        expected_instance_snapshot: dict[str, Any],
        instance_updates: dict[str, Any],
        activations: list[dict[str, Any]],
        execution_updates: list[dict[str, Any]] | None = None,
        project_event: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if instance_id != INSTANCE_ID:
            return {"outcome": "not_found"}
        if self.project["state"] in {"COMPLETED", "FAILED", "CANCELLED", "ARCHIVED"}:
            return {"outcome": "project_terminal"}
        if project_event is not None:
            return {"outcome": "instance_changed"}
        if self.instance["status"] not in expected_statuses:
            return {"outcome": "instance_changed"}
        if self.instance["active_node_ids"] != expected_active_node_ids:
            return {"outcome": "instance_changed"}
        for field, expected in expected_instance_snapshot.items():
            if self.instance.get(field) != expected:
                return {"outcome": "instance_changed"}
        for update in execution_updates or []:
            expected = update["expected"]
            row = next((item for item in self.executions if item["id"] == expected["id"]), None)
            if row is None or any(row.get(key) != value for key, value in expected.items()):
                return {"outcome": "instance_changed"}
        for update in execution_updates or []:
            row = next(item for item in self.executions if item["id"] == update["expected"]["id"])
            row.update(deepcopy(update["values"]))
        created: list[dict[str, Any]] = []
        for activation in activations:
            row = {
                "id": self._next_execution_id,
                "instance_id": instance_id,
                "node_type": None,
                "node_label": None,
                "output_json": None,
                "error": None,
                "retry_count": 0,
                "started_at": datetime.now(UTC),
                "completed_at": None,
                **deepcopy(activation),
            }
            self._next_execution_id += 1
            self.executions.append(row)
            created.append(row)
        self.instance.update(deepcopy(instance_updates))
        self.instance["updated_at"] = datetime.now(UTC)
        return {"outcome": "applied", "instance": deepcopy(self.instance), "executions": deepcopy(created)}


class _NoOpAdapter:
    runtime_type = "test-fixture-queued"

    def __init__(self) -> None:
        from mas_core.worker_contract import CheckpointMode

        self.capabilities = type(
            "Capabilities",
            (),
            {"required_model_capabilities": frozenset(), "checkpoint_mode": CheckpointMode.UNSUPPORTED},
        )()


def _node_environment(api_url: str, node_path: str, dashboard_root: Path) -> dict[str, str]:
    environment = {
        "PATH": node_path,
        "ORCHESTRATOR_URL": api_url,
        "AIAT_OPERATOR_API_KEY": API_KEY,
    }
    for name in ("SYSTEMROOT", "WINDIR", "TEMP", "TMP"):
        value = os.environ.get(name)
        if value:
            environment[name] = value
    # Do not inherit ambient provider or application credentials into Node.
    environment["NODE_PATH"] = str(dashboard_root / "node_modules")
    return environment


def _run_bff(
    *,
    mode: str,
    api_url: str,
    node_path: str,
    dashboard_root: Path,
    run_id: UUID | None = None,
) -> dict[str, Any]:
    destination = urlsplit(api_url)
    assert (
        destination.scheme == "http"
        and destination.hostname == "127.0.0.1"
        and destination.port is not None
        and 32768 <= destination.port <= 65535
        and not destination.username
        and not destination.password
        and destination.path == ""
        and not destination.query
        and not destination.fragment
    ), "the BFF integration harness must reject non-loopback destinations"
    script = dashboard_root / "scripts" / "test-research-brief-bff-api.mjs"
    arguments = [node_path, str(script), mode]
    if run_id is not None:
        arguments.append(str(run_id))
    completed = subprocess.run(
        arguments,
        cwd=dashboard_root,
        env=_node_environment(api_url, node_path, dashboard_root),
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    assert completed.returncode == 0, "BFF harness process failed"
    return json.loads(completed.stdout)


@pytest.mark.parametrize(
    "api_url",
    [
        "http://127.0.0.1:8000/",
        "http://127.0.0.1:32767/",
        "http://localhost:40000/",
        "https://127.0.0.1:40000/",
        "http://example.invalid:40000/",
        "http://127.0.0.1:40000/?destination=operational",
    ],
    ids=["fixed-api-port", "non-ephemeral-port", "hostname-alias", "tls-endpoint", "external-host", "query-string"],
)
def test_bff_harness_rejects_non_test_destinations_before_spawning_node(
    monkeypatch: pytest.MonkeyPatch,
    api_url: str,
) -> None:
    def unexpected_spawn(*_args: Any, **_kwargs: Any) -> subprocess.CompletedProcess[str]:
        raise AssertionError("an untrusted destination reached subprocess.run")

    monkeypatch.setattr(subprocess, "run", unexpected_spawn)
    with pytest.raises(AssertionError, match="must reject non-loopback destinations"):
        _run_bff(
            mode="read-instance",
            api_url=api_url,
            node_path="unused",
            dashboard_root=Path("."),
        )


@pytest.mark.parametrize(
    "api_url",
    ["http://127.0.0.1:8000/", "http://example.invalid:40000/"],
    ids=["operational-loopback-port", "external-host"],
)
def test_node_bff_harness_rejects_non_test_destinations_before_route_invocation(
    api_url: str,
) -> None:
    node_path, dashboard_root = _node_and_dashboard()
    completed = subprocess.run(
        [node_path, str(dashboard_root / "scripts" / "test-research-brief-bff-api.mjs"), "read-instance"],
        cwd=dashboard_root,
        env=_node_environment(api_url, node_path, dashboard_root),
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )

    assert completed.returncode != 0
    assert completed.stdout == ""
    assert "ephemeral loopback API destination" in completed.stderr


def _start_test_api(monkeypatch: pytest.MonkeyPatch, storage: _ResearchBriefStorage):
    from orchestrator_api import main

    monkeypatch.setenv("AIAT_OPERATOR_API_KEY", API_KEY)
    monkeypatch.setenv("MAS_API_KEY", "synthetic-service-key")
    monkeypatch.setenv("PM_GATEWAY_API_KEY", "synthetic-pm-key")
    monkeypatch.delenv("AIAT_CEO_API_KEY", raising=False)
    monkeypatch.delenv("AIAT_WORKER_API_KEY", raising=False)
    monkeypatch.delenv("GATEWAY_API_KEY", raising=False)
    monkeypatch.setenv("MAS_ENVIRONMENT", "production")

    app = main.app
    previous = {
        "storage": getattr(app.state, "storage", None),
        "controller": getattr(app.state, "controller", None),
        "local_host_queue_config": getattr(app.state, "local_host_queue_config", None),
        "local_host_queue_runtime": getattr(app.state, "local_host_queue_runtime", None),
        "worker_run_tasks": getattr(app.state, "worker_run_tasks", None),
    }
    app.state.storage = storage
    app.state.local_host_queue_config = None
    app.state.local_host_queue_runtime = None
    app.state.worker_run_tasks = set()

    async def certified_fixture_adapter(_storage: Any, _worker: dict[str, Any], **_kwargs: Any):
        return _NoOpAdapter()

    monkeypatch.setattr(main, "_certified_worker_adapter", certified_fixture_adapter)

    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", 0))
    listener.listen(128)
    port = int(listener.getsockname()[1])
    server = uvicorn.Server(
        uvicorn.Config(
            app,
            host="127.0.0.1",
            port=port,
            lifespan="off",
            log_level="critical",
            access_log=False,
            log_config=None,
        )
    )

    def serve() -> None:
        asyncio.run(server.serve(sockets=[listener]))

    thread = threading.Thread(target=serve, name="research-brief-test-api", daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started and thread.is_alive() and time.monotonic() < deadline:
        time.sleep(0.02)
    assert server.started, "test-only API failed to bind loopback"

    def stop() -> None:
        server.should_exit = True
        thread.join(timeout=10)
        listener.close()
        for key, value in previous.items():
            if value is None:
                with suppress(AttributeError):
                    delattr(app.state, key)
            else:
                setattr(app.state, key, value)

    return f"http://127.0.0.1:{port}", stop


def _node_and_dashboard() -> tuple[str, Path]:
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is required for the real dashboard BFF route harness")
    dashboard = Path(__file__).resolve().parents[2] / "mas-dashboard"
    if not (dashboard / "node_modules" / "typescript").exists():
        pytest.skip("dashboard TypeScript runtime is not installed")
    return node, dashboard


@pytest.mark.parametrize(
    ("research_output", "expected_status", "expected_admissions"),
    [
        (
            {
                "research_summary": "Synthetic findings persisted by the test fixture.",
                "sources": [{"evidence_id": "source-1", "label": "Test source"}],
            },
            200,
            2,
        ),
        ({"sources": [{"evidence_id": "source-1", "label": "Test source"}]}, 422, 1),
        (
            {
                "research_summary": ["wrong type"],
                "sources": [{"evidence_id": "source-1", "label": "Test source"}],
            },
            422,
            1,
        ),
    ],
    ids=["report-receives-research-context", "missing-summary-blocks-admission", "invalid-summary-blocks-admission"],
)
def test_real_dashboard_bff_and_api_validate_research_output_before_report_admission(
    monkeypatch: pytest.MonkeyPatch,
    research_output: dict[str, Any],
    expected_status: int,
    expected_admissions: int,
) -> None:
    """BFF mutations drive real API handlers over loopback with fake storage only."""

    node_path, dashboard_root = _node_and_dashboard()
    storage = _ResearchBriefStorage()
    api_url, stop_api = _start_test_api(monkeypatch, storage)
    try:
        research_admission = _run_bff(
            mode="advance-research",
            api_url=api_url,
            node_path=node_path,
            dashboard_root=dashboard_root,
        )
        assert research_admission["status"] == 200, research_admission
        assert len(storage.admissions) == 1
        research_run = storage.admissions[0]
        assert research_run["task_type"] == "research.collect"
        assert research_run["request_json"]["task_input"] == {"query": "AIAT task input contract"}

        research_run_id = research_run["id"]
        storage.worker_runs[research_run_id].update(
            {
                "state": "SUCCEEDED",
                "result_json": {
                    "run_id": str(research_run_id),
                    "worker_id": str(RESEARCH_WORKER_ID),
                    "success": True,
                    "output": research_output,
                },
            }
        )

        completion = _run_bff(
            mode="complete-research",
            api_url=api_url,
            node_path=node_path,
            dashboard_root=dashboard_root,
            run_id=research_run_id,
        )
        assert completion["status"] == expected_status, completion
        assert len(storage.admissions) == expected_admissions

        instance_read = _run_bff(
            mode="read-instance",
            api_url=api_url,
            node_path=node_path,
            dashboard_root=dashboard_root,
        )
        assert instance_read["status"] == 200, instance_read
        persisted_context = instance_read["body"]["context_json"]
        assert persisted_context.get("research_summary") == research_output.get("research_summary")

        if expected_status == 200:
            report_run = storage.admissions[1]
            assert report_run["task_type"] == "research.report"
            report_input = report_run["request_json"]["task_input"]
            report_context = report_input["flow_context"]
            assert report_context["brief_topic"] == "AIAT task input contracts"
            assert report_context["research_summary"] == research_output["research_summary"]
            assert report_context["sources"] == research_output["sources"]
            assert report_context["worker_run_id"] == str(research_run_id)
            assert report_input["flow_node"] == {"id": "report", "label": "Write report"}
            assert "report" not in report_context.get("active_worker_runs", {})
            assert report_run["state"] == "QUEUED"
        else:
            assert [row["task_type"] for row in storage.admissions] == ["research.collect"]
            assert persisted_context.get("active_worker_runs", {}).get("report") is None
            assert any(
                row["node_id"] == "report" and row["status"] == "RUNNING"
                for row in storage.executions
            )
    finally:
        stop_api()

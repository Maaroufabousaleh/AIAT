"""Hermetic BFF-to-API Research Brief execution and evidence integration.

The dashboard BFF, orchestrator API, WorkerRunController, mediated-tool bridge,
artifact registrar, and project-evidence routes are real source code. Storage,
Tool Service, and blob persistence are explicit in-memory test dependencies;
the worker is a synthetic in-process adapter and makes no external calls.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import shutil
import socket
import subprocess
import threading
import time
from contextlib import suppress
from copy import deepcopy
from datetime import UTC, datetime, timedelta
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
COMPANY_ID = UUID("50000000-0000-4000-8000-000000000508")
DEFAULT_COMPANY_ID = UUID("00000000-0000-4000-8000-000000000001")
MODEL_PROFILE_ID = "research-approved-fixture"
LEGACY_TEXT_DOCUMENT_ID = UUID("50000000-0000-4000-8000-000000000509")
LEGACY_BLOB_DOCUMENT_ID = UUID("50000000-0000-4000-8000-000000000510")
LEGACY_TEXT_CONTENT = "Legacy inline project note without a stored checksum."
LEGACY_BLOB_CONTENT = b"# Legacy blob document without a stored checksum\n"
LEGACY_BLOB_BUCKET = "synthetic-research-brief"
LEGACY_BLOB_KEY = f"{PROJECT_ID}/legacy/legacy-brief.md"


def _flow_definition() -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "metadata": {"template_id": "research", "evidence_policy": "operations"},
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
                "id": "review",
                "type": "approval",
                "label": "Operator review",
                "config": {"approver_role": "human_operator"},
            },
            {
                "id": "report",
                "type": "task",
                "label": "Write report",
                "config": {
                    "worker_id": str(REPORT_WORKER_ID),
                    "model_mode": "none",
                    "task_type": "research.report",
                    "tool_grants": ["file_write"],
                    "permission_requirements": ["project.write"],
                    # Omitted input deliberately uses the documented legacy
                    # flow_context/flow_node task contract.
                },
            },
            {"id": "end", "type": "end", "label": "Complete", "config": {}},
        ],
        "edges": [
            {"id": "start-research", "source": "start", "target": "research"},
            {"id": "research-review", "source": "research", "target": "review"},
            {"id": "review-report", "source": "review", "target": "report"},
            {"id": "report-end", "source": "report", "target": "end"},
        ],
    }


class _ResearchBriefStorage:
    def __init__(
        self,
        *,
        research_output: dict[str, Any],
        report_tool_outcome: str = "success",
        require_report_document: bool = False,
        include_legacy_documents: bool = False,
    ) -> None:
        now = datetime.now(UTC)
        self.research_output = deepcopy(research_output)
        self.report_tool_outcome = report_tool_outcome
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
            "company_id": COMPANY_ID,
            "revision": 0,
            "config": {
                "evidence_policy": (
                    {
                        "policy_id": "research_delivery",
                        "version": "1.0",
                        "requirements": {
                            "requires_flow_terminal": True,
                            "requires_artifacts": True,
                            "requires_documents": True,
                            "required_document_types": ["RESEARCH_BRIEF"],
                            "requires_document_bodies": True,
                        },
                    }
                    if require_report_document
                    else "operations"
                ),
            },
            "created_by": "test-operator",
            "created_at": now,
            "updated_at": now,
        }
        self.instance = {
            "id": INSTANCE_ID,
            "project_id": PROJECT_ID,
            "flow_id": FLOW_ID,
            "flow_version": 1,
            "status": "NOT_STARTED",
            "active_node_ids": [],
            "context_json": {"brief_topic": "AIAT task input contracts"},
            "started_at": now,
            "created_at": now,
            "updated_at": now,
            "retry_count": 0,
        }
        self.executions: list[dict[str, Any]] = []
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
        self.worker_events: dict[UUID, list[dict[str, Any]]] = {}
        self.worker_tool_effects: dict[tuple[UUID, str], dict[str, Any]] = {}
        self.worker_artifacts: dict[UUID, list[dict[str, Any]]] = {}
        self.worker_usage: dict[UUID, list[dict[str, Any]]] = {}
        self.project_usage_events: list[dict[str, Any]] = []
        self.artifacts: list[dict[str, Any]] = []
        self.documents: list[dict[str, Any]] = []
        self.approval_gates: list[dict[str, Any]] = []
        self.project_history: list[dict[str, Any]] = []
        self.blob_objects: dict[tuple[str, str], bytes] = {}
        if include_legacy_documents:
            self.documents.extend(
                [
                    {
                        "id": LEGACY_TEXT_DOCUMENT_ID,
                        "project_id": PROJECT_ID,
                        "doc_type": "LEGACY_TEXT",
                        "version": 1,
                        "content_text": LEGACY_TEXT_CONTENT,
                        "created_by": "test-operator",
                        "status": "DRAFT",
                        "created_at": now,
                    },
                    {
                        "id": LEGACY_BLOB_DOCUMENT_ID,
                        "project_id": PROJECT_ID,
                        "doc_type": "LEGACY_BLOB",
                        "version": 1,
                        "blob_bucket": LEGACY_BLOB_BUCKET,
                        "blob_key": LEGACY_BLOB_KEY,
                        "created_by": "test-operator",
                        "status": "DRAFT",
                        "created_at": now,
                    },
                ]
            )
            self.blob_objects[(LEGACY_BLOB_BUCKET, LEGACY_BLOB_KEY)] = LEGACY_BLOB_CONTENT
        self.tool_dispatches: list[dict[str, Any]] = []
        self.adapter_tool_responses: dict[UUID, dict[str, Any]] = {}
        self.report_inputs: dict[UUID, dict[str, Any]] = {}
        self.report_contents: dict[UUID, str] = {}
        self._next_execution_id = 1
        self._next_artifact_id = 1
        self.model_snapshots: list[dict[str, Any]] = []
        self.engine = _InMemoryApprovalEngine(self)

    async def get_project(self, project_id: UUID) -> dict[str, Any] | None:
        return deepcopy(self.project) if project_id == PROJECT_ID else None

    async def list_task_logs(self, **_kwargs: Any) -> list[dict[str, Any]]:
        return []

    async def list_workers(self) -> list[dict[str, Any]]:
        return [deepcopy(worker) for worker in self.workers.values()]

    async def list_issues(self, **_kwargs: Any) -> list[dict[str, Any]]:
        return []

    async def list_project_context(
        self,
        project_id: UUID,
        *,
        item_type: str | None = None,
        tags: list[str] | None = None,
        include_document_revisions: bool = False,
    ) -> list[dict[str, Any]]:
        if item_type and item_type.upper() != "DOCUMENT":
            return []
        from mas_core.memory.storage import document_to_context_item

        documents = [row for row in self.documents if row["project_id"] == project_id]
        if not include_document_revisions:
            latest_by_lineage: dict[str, dict[str, Any]] = {}
            for document in documents:
                lineage_id = str(document.get("lineage_id") or document["id"])
                current = latest_by_lineage.get(lineage_id)
                if current is None or int(document.get("version") or 1) >= int(current.get("version") or 1):
                    latest_by_lineage[lineage_id] = document
            documents = list(latest_by_lineage.values())
        items = [document_to_context_item(document) for document in documents]
        if tags:
            requested_tags = {tag.strip().lower() for tag in tags if tag.strip()}
            items = [
                item
                for item in items
                if requested_tags.intersection(str(tag).lower() for tag in item.get("tags") or [])
            ]
        return sorted(items, key=lambda item: str(item.get("created_at") or ""), reverse=True)

    async def get_project_repository_record(self, _project_id: UUID) -> None:
        return None

    async def get_project_usage(self, _project_id: UUID) -> dict[str, Any]:
        return {
            "available": True,
            "llm_calls": 0,
            "tool_calls": 0,
            "failed_calls": 0,
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
            "total_cost_usd": 0.0,
        }

    async def list_company_worker_assignments(self, company_id: UUID) -> list[dict[str, Any]]:
        if company_id != COMPANY_ID:
            return []
        return [
            {
                "company_id": COMPANY_ID,
                "worker_id": worker_id,
                "status": "ACTIVE",
                "approval_required": False,
                "tool_grants": ["file_write"],
                "permission_grants": ["project.write"],
            }
            for worker_id in self.workers
        ]

    async def get_worker(self, worker_id: UUID) -> dict[str, Any] | None:
        worker = self.workers.get(worker_id)
        return deepcopy(worker) if worker else None

    async def list_capabilities(self) -> list[dict[str, Any]]:
        return deepcopy(self.capabilities)

    async def get_flow(self, flow_id: UUID) -> dict[str, Any] | None:
        return deepcopy(self.flow) if flow_id == FLOW_ID else None

    async def get_flow_instance(self, instance_id: UUID) -> dict[str, Any] | None:
        return deepcopy(self.instance) if instance_id == INSTANCE_ID else None

    async def get_flow_instance_by_project(self, project_id: UUID) -> dict[str, Any] | None:
        return deepcopy(self.instance) if project_id == PROJECT_ID else None

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

    async def get_flow_node_execution(self, execution_id: int) -> dict[str, Any] | None:
        row = next((item for item in self.executions if item["id"] == execution_id), None)
        return deepcopy(row) if row else None

    async def get_approval_gate_for_flow_execution(self, execution_id: int) -> dict[str, Any] | None:
        row = next(
            (item for item in self.approval_gates if item.get("flow_node_execution_id") == execution_id),
            None,
        )
        return deepcopy(row) if row else None

    async def list_approval_gates(
        self,
        *,
        project_id: UUID | None = None,
        status: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        rows = self.approval_gates
        if project_id is not None:
            rows = [row for row in rows if row.get("project_id") == project_id]
        if status is not None:
            rows = [row for row in rows if row.get("status") == status]
        return deepcopy(rows[:limit])

    async def resolve_flow_approval_gate(
        self,
        gate_id: UUID,
        *,
        project_id: UUID,
        instance_id: UUID,
        execution_id: int,
        node_id: str,
        status: str,
        decided_by: str,
        justification: str | None,
        human_input: dict[str, Any] | None,
        execution_output: dict[str, Any],
        instance_updates: dict[str, Any],
        activated_executions: list[dict[str, Any]],
        expected_instance_snapshot: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        gate = next((row for row in self.approval_gates if row["id"] == gate_id), None)
        execution = next((row for row in self.executions if row["id"] == execution_id), None)
        if gate is None or execution is None or gate["project_id"] != project_id:
            return {"outcome": "not_found"}
        if gate["status"] != "PENDING":
            return {"outcome": "conflict"}
        if (
            self.instance["id"] != instance_id
            or self.instance["project_id"] != project_id
            or execution["instance_id"] != instance_id
            or execution["node_id"] != node_id
            or execution["status"] != "RUNNING"
            or not self._snapshot_matches(self.instance, expected_instance_snapshot or {})
        ):
            return {"outcome": "stale_instance"}
        gate.update(
            {
                "status": status,
                "decided_by": decided_by,
                "justification": justification,
                "human_input": deepcopy(human_input),
                "decided_at": datetime.now(UTC),
            }
        )
        activated_ids: list[str] = []
        execution_output = deepcopy(execution_output)
        for activation in activated_executions:
            row = self._new_execution(instance_id, activation)
            activated_ids.append(str(row["node_id"]))
        execution.update(
            {
                "status": "COMPLETED",
                "output_json": {
                    **execution_output,
                    "approval_gate_id": str(gate_id),
                    "activated_node_ids": activated_ids,
                },
                "completed_at": datetime.now(UTC),
            }
        )
        self.instance.update(deepcopy(instance_updates))
        self.instance["updated_at"] = datetime.now(UTC)
        return {
            "outcome": "applied",
            "gate": deepcopy(gate),
            "execution": deepcopy(execution),
            "instance": deepcopy(self.instance),
            "activated_node_ids": activated_ids,
        }

    async def get_worker_run(self, run_id: UUID) -> dict[str, Any] | None:
        row = self.worker_runs.get(run_id)
        return deepcopy(row) if row else None

    async def create_model_resolution_snapshot(self, **kwargs: Any) -> dict[str, Any]:
        snapshot = deepcopy(kwargs.get("snapshot"))
        snapshot_id = snapshot.get("snapshot_id")
        row = {
            "id": UUID(str(snapshot_id)) if snapshot_id is not None else uuid4(),
            "project_id": kwargs.get("project_id"),
            "provider_id": snapshot.get("provider_id"),
            "exact_model_id": snapshot.get("exact_model_id"),
            "snapshot": snapshot,
        }
        self.model_snapshots.append(deepcopy(row))
        return deepcopy(row)

    async def get_model_resolution_snapshot(
        self,
        snapshot_id: UUID,
        *,
        project_id: UUID | None = None,
    ) -> dict[str, Any] | None:
        row = next(
            (item for item in self.model_snapshots if item["id"] == snapshot_id),
            None,
        )
        if row is None or (row["project_id"] is not None and row["project_id"] != project_id):
            return None
        return deepcopy(row)

    async def create_worker_run(self, **kwargs: Any) -> dict[str, Any]:
        existing = next(
            (
                row
                for row in self.worker_runs.values()
                if row["worker_id"] == kwargs["worker_id"]
                and row.get("idempotency_key") == kwargs["idempotency_key"]
            ),
            None,
        )
        if existing is not None:
            return deepcopy(existing)
        row = {
            "id": kwargs["run_id"],
            "worker_id": kwargs["worker_id"],
            "worker_shell_version_id": kwargs.get("worker_shell_version_id"),
            "adapter_id": kwargs.get("adapter_id"),
            "steward_id": kwargs.get("steward_id"),
            "model_resolution_snapshot_id": kwargs.get("model_resolution_snapshot_id"),
            "idempotency_key": kwargs["idempotency_key"],
            "project_id": kwargs["project_id"],
            "flow_id": kwargs["flow_id"],
            "flow_instance_id": kwargs["flow_instance_id"],
            "flow_node_execution_id": kwargs["flow_node_execution_id"],
            "task_type": kwargs["task_type"],
            "request_json": deepcopy(kwargs["request"]),
            "state": kwargs["state"],
            "result_json": None,
            "error_json": None,
            "claim_owner": None,
            "attempt_count": 0,
            "lease_expires_at": None,
            "cancel_requested_at": None,
            "created_at": datetime.now(UTC),
        }
        self.worker_runs[row["id"]] = row
        self.admissions.append(deepcopy(row))
        self.worker_events[row["id"]] = []
        self.worker_artifacts[row["id"]] = []
        self.worker_usage[row["id"]] = []
        return deepcopy(row)

    async def request_worker_run_cancel(self, run_id: UUID) -> dict[str, Any] | None:
        row = self.worker_runs.get(run_id)
        if row is None:
            return None
        row["cancel_requested_at"] = datetime.now(UTC)
        return deepcopy(row)

    async def claim_worker_run(self, *, owner: str, lease_seconds: int, run_id: UUID) -> dict[str, Any] | None:
        row = self.worker_runs.get(run_id)
        if row is None or row["state"] != "QUEUED":
            return None
        row.update(
            {
                "state": "CLAIMED",
                "claim_owner": owner,
                "attempt_count": int(row.get("attempt_count") or 0) + 1,
                "lease_expires_at": datetime.now(UTC) + timedelta(seconds=lease_seconds),
                "heartbeat_at": datetime.now(UTC),
            }
        )
        return deepcopy(row)

    async def heartbeat_worker_run(
        self,
        run_id: UUID,
        *,
        owner: str,
        attempt_count: int,
        lease_seconds: int,
    ) -> dict[str, Any] | None:
        row = self.worker_runs.get(run_id)
        if not self._claim_matches(row, owner, attempt_count, allowed_states=None):
            return None
        row["lease_expires_at"] = datetime.now(UTC) + timedelta(seconds=lease_seconds)
        row["heartbeat_at"] = datetime.now(UTC)
        return deepcopy(row)

    @staticmethod
    def _claim_matches(
        row: dict[str, Any] | None,
        owner: str | None,
        attempt_count: int,
        *,
        allowed_states: set[str] | None,
    ) -> bool:
        if row is None or row.get("claim_owner") != owner or row.get("attempt_count") != attempt_count:
            return False
        if allowed_states is not None and row.get("state") not in allowed_states:
            return False
        lease = row.get("lease_expires_at")
        return lease is not None and lease > datetime.now(UTC) and row.get("cancel_requested_at") is None

    async def validate_worker_run_claim(
        self,
        *,
        run_id: UUID,
        claim_owner: str | None,
        attempt_count: int,
    ) -> bool:
        return self._claim_matches(
            self.worker_runs.get(run_id),
            claim_owner,
            attempt_count,
            allowed_states={"RUNNING", "PAUSING"},
        )

    async def transition_worker_run(
        self,
        run_id: UUID,
        *,
        new_state: str,
        expected_state: str | None = None,
        result: dict[str, Any] | None = None,
        error: dict[str, Any] | None = None,
        negotiation: dict[str, Any] | None = None,
        replay_metadata: dict[str, Any] | None = None,
        transition_metadata: dict[str, Any] | None = None,
        claim_owner: str | None = None,
        attempt_count: int | None = None,
        **_kwargs: Any,
    ) -> dict[str, Any] | None:
        row = self.worker_runs.get(run_id)
        if row is None or (expected_state is not None and row["state"] != expected_state):
            return None
        if (
            (row.get("claim_owner") is not None or int(row.get("attempt_count") or 0) > 0)
            and not self._claim_matches(
                row,
                claim_owner,
                int(attempt_count or 0),
                allowed_states=None,
            )
        ):
            return None
        row["state"] = new_state
        if result is not None:
            row["result_json"] = deepcopy(result)
        if error is not None:
            row["error_json"] = deepcopy(error)
        if negotiation is not None:
            row["negotiation_json"] = deepcopy(negotiation)
        if replay_metadata is not None:
            row["replay_metadata"] = deepcopy(replay_metadata)
        if transition_metadata:
            row.setdefault("transition_metadata", {}).update(deepcopy(transition_metadata))
            binding = transition_metadata.get("runtime_binding")
            if isinstance(binding, dict):
                row["runtime_binding"] = deepcopy(binding)
                row["runtime_run_id"] = binding.get("runtime_run_id")
        if new_state in {"SUCCEEDED", "FAILED", "CANCELLED", "TIMED_OUT"}:
            row.update(
                {
                    "completed_at": datetime.now(UTC),
                    "claim_owner": None,
                    "lease_expires_at": None,
                }
            )
        return deepcopy(row)

    async def append_worker_event(self, *, run_id: UUID, sequence: int, event_type: str, event: dict[str, Any], event_sha256: str, **_kwargs: Any) -> dict[str, Any]:
        rows = self.worker_events.setdefault(run_id, [])
        row = {"id": uuid4(), "run_id": run_id, "sequence": sequence, "event_type": event_type, "event_json": deepcopy(event), "event_sha256": event_sha256}
        existing = next((item for item in rows if item["sequence"] == sequence), None)
        if existing is not None:
            if existing["event_sha256"] != event_sha256:
                raise ValueError("duplicate event conflict")
            return deepcopy(existing)
        rows.append(row)
        return deepcopy(row)

    async def begin_worker_tool_effect(
        self,
        *,
        run_id: UUID,
        request_id: UUID,
        idempotency_key: str,
        tool_name: str,
        request_sha256: str,
        claim_owner: str | None = None,
        attempt_count: int | None = None,
    ) -> dict[str, Any]:
        if not await self.validate_worker_run_claim(
            run_id=run_id,
            claim_owner=claim_owner,
            attempt_count=int(attempt_count or 0),
        ):
            return {"disposition": "CLAIM_LOST"}
        key = (run_id, idempotency_key)
        row = self.worker_tool_effects.get(key)
        if row is not None:
            if row["request_sha256"] != request_sha256:
                return {**deepcopy(row), "disposition": "CONFLICT"}
            disposition = "REPLAY" if row["state"] == "COMPLETED" else "AMBIGUOUS"
            return {**deepcopy(row), "disposition": disposition}
        row = {
            "id": uuid4(),
            "run_id": run_id,
            "request_id": request_id,
            "idempotency_key": idempotency_key,
            "tool_name": tool_name,
            "request_sha256": request_sha256,
            "state": "IN_FLIGHT",
            "response_json": None,
            "created_at": datetime.now(UTC),
        }
        self.worker_tool_effects[key] = row
        return {**deepcopy(row), "disposition": "NEW"}

    async def complete_worker_tool_effect(
        self,
        effect_id: UUID,
        *,
        run_id: UUID,
        claim_owner: str | None,
        attempt_count: int,
        state: str,
        response_json: dict[str, Any],
    ) -> dict[str, Any] | None:
        row = next((item for item in self.worker_tool_effects.values() if item["id"] == effect_id), None)
        if row is None or row["run_id"] != run_id or row["state"] != "IN_FLIGHT":
            return None
        if not await self.validate_worker_run_claim(
            run_id=run_id,
            claim_owner=claim_owner,
            attempt_count=attempt_count,
        ):
            return None
        row.update(
            {
                "state": state,
                "response_json": deepcopy(response_json),
                "updated_at": datetime.now(UTC),
                "completed_at": datetime.now(UTC),
            }
        )
        return deepcopy(row)

    async def list_worker_tool_effects(self, run_id: UUID, *, limit: int = 1000) -> list[dict[str, Any]]:
        rows = [row for row in self.worker_tool_effects.values() if row["run_id"] == run_id]
        return [
            {
                **deepcopy(row),
                "response_success": bool((row.get("response_json") or {}).get("success")),
            }
            for row in rows[:limit]
        ]

    async def complete_worker_run_success_claim_fenced(self, **kwargs: Any) -> dict[str, Any] | None:
        return await self._complete_claimed_run(terminal_state="SUCCEEDED", **kwargs)

    async def complete_worker_run_failure_claim_fenced(self, **kwargs: Any) -> dict[str, Any] | None:
        return await self._complete_claimed_run(terminal_state="FAILED", **kwargs)

    async def _complete_claimed_run(
        self,
        *,
        run_id: UUID,
        claim_owner: str | None,
        attempt_count: int,
        artifacts: list[dict[str, Any]],
        usage: dict[str, Any],
        result: dict[str, Any],
        terminal_state: str,
        error: dict[str, Any] | None = None,
        replay_metadata: dict[str, Any] | None = None,
        project_usage: dict[str, Any] | None = None,
        **_kwargs: Any,
    ) -> dict[str, Any] | None:
        row = self.worker_runs.get(run_id)
        if not self._claim_matches(
            row,
            claim_owner,
            attempt_count,
            allowed_states={"RUNNING", "PAUSING"},
        ):
            return None
        assert row is not None
        unresolved = any(
            effect["run_id"] == run_id and effect["state"] in {"IN_FLIGHT", "AMBIGUOUS"}
            for effect in self.worker_tool_effects.values()
        )
        if terminal_state == "SUCCEEDED" and unresolved:
            terminal_state = "FAILED"
            error = {
                "code": "WORKER_TOOL_EFFECT_UNRESOLVED",
                "message": "worker success cannot be committed while a mediated tool effect is unresolved",
                "retryable": False,
                "terminal": True,
                "category": "recovery",
                "details": {"effect_state": "IN_FLIGHT_OR_AMBIGUOUS"},
            }
            result = {**deepcopy(result), "success": False, "error": error}
        for artifact in artifacts:
            artifact_id = self._next_artifact_id
            self._next_artifact_id += 1
            stored_artifact = {
                "id": artifact_id,
                "agent_id": artifact["agent_id"],
                "path": artifact["path"],
                "uri": artifact["uri"],
                "metadata": deepcopy(artifact["metadata"]),
                "sha256": artifact["sha256"],
                "size_bytes": artifact["size_bytes"],
                "kind": artifact["kind"],
                "trace_id": artifact.get("trace_id"),
                "span_id": artifact.get("span_id"),
                "created_at": datetime.now(UTC),
            }
            self.artifacts.append(stored_artifact)
            self.worker_artifacts.setdefault(run_id, []).append(
                {
                    "id": uuid4(),
                    "run_id": run_id,
                    "artifact_id": artifact_id,
                    "kind": artifact["kind"],
                    "uri": artifact["uri"],
                    "sha256": artifact["sha256"],
                    "size_bytes": artifact["size_bytes"],
                    "metadata": deepcopy(artifact["metadata"]),
                }
            )
        self.worker_usage.setdefault(run_id, []).append(
            {"id": uuid4(), "run_id": run_id, **deepcopy(usage), "created_at": datetime.now(UTC)}
        )
        if project_usage is not None:
            self.project_usage_events.append({"id": uuid4(), **deepcopy(project_usage)})
        row.update(
            {
                "state": terminal_state,
                "result_json": deepcopy(result),
                "error_json": deepcopy(error) if terminal_state == "FAILED" else None,
                "replay_metadata": deepcopy(replay_metadata or {}),
                "completed_at": datetime.now(UTC),
                "claim_owner": None,
                "lease_expires_at": None,
            }
        )
        return deepcopy(row)

    async def list_worker_artifacts(self, run_id: UUID, *, limit: int = 1000) -> list[dict[str, Any]]:
        return deepcopy(self.worker_artifacts.get(run_id, [])[:limit])

    async def list_worker_usage(self, run_id: UUID, *, limit: int = 1000) -> list[dict[str, Any]]:
        return deepcopy(self.worker_usage.get(run_id, [])[:limit])

    async def list_worker_runs(
        self,
        *,
        project_id: UUID | None = None,
        worker_id: UUID | None = None,
        flow_instance_id: UUID | None = None,
        state: str | None = None,
        limit: int = 1000,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        rows = list(self.worker_runs.values())
        if project_id is not None:
            rows = [row for row in rows if row.get("project_id") == project_id]
        if worker_id is not None:
            rows = [row for row in rows if row.get("worker_id") == worker_id]
        if flow_instance_id is not None:
            rows = [row for row in rows if row.get("flow_instance_id") == flow_instance_id]
        if state is not None:
            rows = [row for row in rows if row.get("state") == state]
        return deepcopy(rows[offset : offset + limit])

    async def list_artifacts(self, *, limit: int = 100) -> list[dict[str, Any]]:
        return deepcopy(self.artifacts[:limit])

    async def list_documents(
        self,
        project_id: UUID,
        *,
        doc_type: str | None = None,
    ) -> list[dict[str, Any]]:
        rows = [row for row in self.documents if row["project_id"] == project_id]
        if doc_type is not None:
            rows = [row for row in rows if row["doc_type"] == doc_type]
        return deepcopy(rows)

    async def create_document(
        self,
        *,
        project_id: UUID,
        doc_type: str,
        created_by: str,
        blob_bucket: str,
        blob_key: str,
        blob_sha256: str,
    ) -> dict[str, Any]:
        existing = next(
            (
                row
                for row in self.documents
                if row["project_id"] == project_id
                and row["doc_type"] == doc_type
                and row["blob_bucket"] == blob_bucket
                and row["blob_key"] == blob_key
                and row["blob_sha256"] == blob_sha256
            ),
            None,
        )
        if existing is not None:
            return deepcopy(existing)
        row = {
            "id": uuid4(),
            "project_id": project_id,
            "doc_type": doc_type,
            "version": 1,
            "created_by": created_by,
            "blob_bucket": blob_bucket,
            "blob_key": blob_key,
            "blob_sha256": blob_sha256,
            "status": "DRAFT",
            "created_at": datetime.now(UTC),
        }
        self.documents.append(row)
        return deepcopy(row)

    async def get_document(self, document_id: UUID) -> dict[str, Any] | None:
        row = next((item for item in self.documents if item["id"] == document_id), None)
        return deepcopy(row) if row else None

    async def get_artifact(self, artifact_id: int) -> dict[str, Any] | None:
        row = next((item for item in self.artifacts if item["id"] == artifact_id), None)
        return deepcopy(row) if row else None

    async def get_project_history(self, project_id: UUID, *, limit: int = 100) -> list[dict[str, Any]]:
        return deepcopy([row for row in self.project_history if row["project_id"] == project_id][-limit:])

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
        supersede_existing: bool = False,
        supersede_reason: str = "Superseded by explicit flow retry",
        **_kwargs: Any,
    ) -> dict[str, Any]:
        if instance_id != INSTANCE_ID:
            return {"outcome": "not_found"}
        if self.project["state"] in {"COMPLETED", "FAILED", "ARCHIVED"}:
            return {"outcome": "project_terminal"}
        if self.project["state"] not in {"IN_PROGRESS", "RETROSPECTIVE", "KPI_PERSISTENCE"}:
            return {"outcome": "project_not_dispatchable"}
        if project_event is not None:
            return {"outcome": "instance_changed"}
        if self.instance["status"] not in expected_statuses:
            return {"outcome": "instance_changed"}
        if self.instance["active_node_ids"] != expected_active_node_ids:
            return {"outcome": "instance_changed"}
        if not self._snapshot_matches(self.instance, expected_instance_snapshot):
            return {"outcome": "instance_changed"}
        for update in execution_updates or []:
            expected = update["expected"]
            row = next((item for item in self.executions if item["id"] == expected["id"]), None)
            if row is None or not self._snapshot_matches(row, expected):
                return {"outcome": "instance_changed"}
        for update in execution_updates or []:
            row = next(item for item in self.executions if item["id"] == update["expected"]["id"])
            row.update(deepcopy(update["values"]))
        if supersede_existing:
            execution_ids = {
                row["id"] for row in self.executions if row["instance_id"] == instance_id
            }
            for gate in self.approval_gates:
                if (
                    gate.get("flow_node_execution_id") in execution_ids
                    and gate.get("status") == "PENDING"
                ):
                    gate.update(
                        {
                            "status": "CANCELLED",
                            "decided_by": "system:flow_retry",
                            "justification": supersede_reason,
                            "decided_at": datetime.now(UTC),
                        }
                    )
            for execution in self.executions:
                if execution["instance_id"] == instance_id and execution["status"] != "SUPERSEDED":
                    execution.update(
                        {
                            "status": "SUPERSEDED",
                            "error": execution.get("error") or supersede_reason,
                            "completed_at": execution.get("completed_at") or datetime.now(UTC),
                        }
                    )
        created: list[dict[str, Any]] = []
        for activation in activations:
            created.append(self._new_execution(instance_id, activation))
        self.instance.update(deepcopy(instance_updates))
        self.instance["updated_at"] = datetime.now(UTC)
        return {"outcome": "applied", "instance": deepcopy(self.instance), "executions": deepcopy(created)}

    def _new_execution(self, instance_id: UUID, activation: dict[str, Any]) -> dict[str, Any]:
        now = datetime.now(UTC)
        row = {
            "id": self._next_execution_id,
            "instance_id": instance_id,
            "node_type": None,
            "node_label": None,
            "output_json": None,
            "error": None,
            "retry_count": 0,
            "started_at": now,
            "completed_at": None,
            **deepcopy(activation),
        }
        self._next_execution_id += 1
        self.executions.append(row)
        if row.get("node_type") == "approval":
            self.approval_gates.append(
                {
                    "id": uuid4(),
                    "project_id": self.project["id"],
                    "flow_node_execution_id": row["id"],
                    "gate_type": row.get("gate_type"),
                    "status": "PENDING",
                    "created_at": now,
                    "decided_by": None,
                }
            )
        return row

    @staticmethod
    def _snapshot_matches(current: dict[str, Any], expected: dict[str, Any]) -> bool:
        return all(current.get(field) == value for field, value in expected.items())


    async def transition_project(
        self,
        project_id: UUID,
        *,
        new_state: str,
        event: str,
        triggered_by: str,
        payload: dict[str, Any] | None = None,
        expected_state: str | None = None,
        expected_revision: int | None = None,
        completion_policy: dict[str, Any] | None = None,
        **_kwargs: Any,
    ) -> dict[str, Any] | None:
        if project_id != PROJECT_ID:
            return None
        current_state = self.project["state"]
        if expected_state is not None and expected_state != current_state:
            return None
        if expected_revision is not None and expected_revision != self.project["revision"]:
            return None
        if new_state == "COMPLETED":
            if completion_policy and completion_policy.get("requires_flow_terminal") and self.instance["status"] != "COMPLETED":
                return None
            if any(row["state"] not in {"SUCCEEDED", "FAILED", "CANCELLED", "TIMED_OUT"} for row in self.worker_runs.values()):
                return None
        now = datetime.now(UTC)
        self.project.update({"state": new_state, "revision": self.project["revision"] + 1, "updated_at": now})
        self.project_history.append(
            {
                "id": uuid4(),
                "project_id": project_id,
                "from_state": current_state,
                "to_state": new_state,
                "event": event,
                "triggered_by": triggered_by,
                "payload": deepcopy(payload),
                "transitioned_at": now,
            }
        )
        if new_state in {"COMPLETED", "FAILED", "ARCHIVED"}:
            for gate in self.approval_gates:
                if gate["project_id"] == project_id and gate["status"] == "PENDING":
                    gate.update({"status": "CANCELLED", "decided_by": triggered_by, "decided_at": now})
        return deepcopy(self.project)


class _UiCreatedResearchBriefStorage(_ResearchBriefStorage):
    """The same persisted test port, left empty for real UI/API creation."""

    def __init__(self, *, research_output: dict[str, Any]) -> None:
        super().__init__(
            research_output=research_output,
            require_report_document=True,
            include_legacy_documents=True,
        )
        self.flow = None
        self.project = None
        self.instance = None
        self.model_governed = True
        for worker_id, capability_id, name in (
            (RESEARCH_WORKER_ID, RESEARCH_CAPABILITY_ID, "Research specialist"),
            (REPORT_WORKER_ID, REPORT_CAPABILITY_ID, "Report specialist"),
        ):
            worker = self.workers[worker_id]
            worker.update(
                {
                    "name": name,
                    "worker_id": str(worker_id),
                    "model_mode": "aiat_gateway",
                    "model_profile_id": MODEL_PROFILE_ID,
                    "capability_names": [
                        capability["name"]
                        for capability in self.capabilities
                        if capability["id"] == capability_id
                    ],
                }
            )
        # The browser creates this company through the production endpoint.
        # Its generated ID is retained by the SQLAlchemy-shaped fake below.
        self.companies: dict[UUID, dict[str, Any]] = {}
        self.created_company_id: UUID | None = None
        self.company_manifest: dict[str, Any] | None = None
        self.company_manifest_versions: list[dict[str, Any]] = []
        self.project_context_items: list[dict[str, Any]] = []
        self.model_profiles = [
            {
                "logical_profile_id": MODEL_PROFILE_ID,
                "purpose": "research",
                "status": "approved",
                "versions": [
                    {
                        "version": "v1",
                        "provider_id": "local-fixture",
                        "exact_model_id": "deterministic-fixture",
                        "status": "approved",
                        "capabilities": ["tool_calling", "streaming"],
                        "constraints_json": {"tool_calling": True, "streaming": True},
                    }
                ],
                "approved_provider_ids": ["local-fixture"],
                "required_capabilities": ["tool_calling", "streaming"],
            }
        ]

    async def get_company(self, company_id: UUID) -> dict[str, Any] | None:
        company = self.companies.get(company_id)
        return deepcopy(company) if company else None

    async def get_company_by_slug(self, slug: str) -> dict[str, Any] | None:
        company = next((row for row in self.companies.values() if row["slug"] == slug), None)
        return deepcopy(company) if company else None

    async def list_companies(self, *, status: str | None = None) -> list[dict[str, Any]]:
        rows = list(self.companies.values())
        if status is not None:
            rows = [row for row in rows if row.get("status") == status]
        return deepcopy(rows)

    async def get_company_manifest(self, company_id: UUID, *, version: int | None = None) -> dict[str, Any] | None:
        if company_id != self.created_company_id:
            return None
        if version is None:
            return deepcopy(self.company_manifest)
        return next(
            (deepcopy(row) for row in self.company_manifest_versions if row["manifest_version"] == version),
            None,
        )

    async def list_company_manifest_versions(
        self, company_id: UUID, *, limit: int = 100
    ) -> list[dict[str, Any]]:
        if company_id != self.created_company_id:
            return []
        return deepcopy(list(reversed(self.company_manifest_versions[-limit:])))

    async def get_company_read_model(self, company_id: UUID) -> dict[str, Any]:
        company = await self.get_company(company_id)
        if company is None:
            return {"company": None, "manifest": None, "departments": [], "assignments": [], "budgets": []}
        manifest = deepcopy(self.company_manifest)
        return {
            "company": company,
            "manifest": manifest,
            "departments": await self.list_company_departments(company_id),
            "assignments": await self.list_company_worker_assignments(company_id),
            "budgets": await self.list_company_budgets(company_id),
        }

    async def list_company_departments(self, company_id: UUID) -> list[dict[str, Any]]:
        if company_id != self.created_company_id or self.company_manifest is None:
            return []
        manifest = self.company_manifest["manifest_json"]
        return [
            {"company_id": company_id, **deepcopy(row)}
            for row in manifest.get("departments", [])
        ]

    async def list_company_worker_assignments(self, company_id: UUID) -> list[dict[str, Any]]:
        if company_id != self.created_company_id or self.company_manifest is None:
            return []
        return [
            {"company_id": company_id, **deepcopy(row)}
            for row in self.company_manifest["manifest_json"].get("worker_assignments", [])
        ]

    async def list_company_budgets(self, company_id: UUID) -> list[dict[str, Any]]:
        if company_id != self.created_company_id or self.company_manifest is None:
            return []
        return [
            {"company_id": company_id, "budget_key": key, "limit_value": value}
            for key, value in self.company_manifest["manifest_json"].get("budgets", {}).items()
        ]

    async def list_budget_reservations(
        self, *, company_id: UUID | None = None, run_id: UUID | None = None, limit: int = 100
    ) -> list[dict[str, Any]]:
        return []

    async def get_budget_state(self, company_id: UUID, budget_key: str) -> dict[str, Any]:
        row = next(
            (row for row in await self.list_company_budgets(company_id) if row["budget_key"] == budget_key),
            None,
        )
        limit = row["limit_value"] if row is not None else 0
        return {
            "company_id": company_id,
            "budget_key": budget_key,
            "configured": row is not None,
            "limit": limit,
            "used": 0,
            "available": limit,
            "currency": "USD",
            "period": "lifetime",
        }

    async def apply_company_manifest(
        self,
        *,
        company_id: UUID,
        manifest: Any,
        digest: str,
        canonical: dict[str, Any],
        source: str,
        actor: str,
        expected_manifest_version: int | None = None,
        compiler_version: str = "aiat-company-compiler/1",
    ) -> dict[str, Any]:
        if company_id not in self.companies:
            raise ValueError("company does not exist")
        current_version = len(self.company_manifest_versions)
        if expected_manifest_version is not None and expected_manifest_version != current_version:
            raise ValueError("COMPANY_MANIFEST_VERSION_CONFLICT")
        now = datetime.now(UTC)
        record = {
            "id": uuid4(),
            "company_id": company_id,
            "manifest_version": current_version + 1,
            "digest": digest,
            "manifest_json": deepcopy(canonical),
            "status": "ACTIVE",
            "source": source,
            "compiled_by": actor,
            "compiled_at": now,
            "created_at": now,
            "compiler_version": compiler_version,
        }
        self.company_manifest_versions.append(record)
        self.company_manifest = deepcopy(record)
        self.companies[company_id]["active_manifest_version_id"] = record["id"]
        self.companies[company_id]["updated_at"] = now
        return await self.get_company_read_model(company_id)

    async def get_model_profile(self, logical_profile_id: str) -> dict[str, Any] | None:
        profile = next(
            (row for row in self.model_profiles if row["logical_profile_id"] == logical_profile_id),
            None,
        )
        return deepcopy(profile) if profile else None

    async def list_model_profiles(self) -> list[dict[str, Any]]:
        return deepcopy(self.model_profiles)

    async def list_workers(
        self, *, team_id: str | None = None, status: str | None = None
    ) -> list[dict[str, Any]]:
        rows = list(self.workers.values())
        if team_id is not None:
            rows = [row for row in rows if row.get("team_id") == team_id]
        if status is not None:
            rows = [row for row in rows if row.get("status") == status]
        return [deepcopy(row) for row in rows]

    async def get_active_runtime_adapter(self, worker_id: UUID) -> dict[str, Any] | None:
        if worker_id not in self.workers:
            return None
        return {
            "id": self.workers[worker_id]["active_adapter_id"],
            "worker_id": worker_id,
            "status": "active",
            "conformance_status": "passed",
            "capabilities_json": {
                "checkpoint_mode": "native",
                "tool_mode": "aiat_mediated",
                "model_mode": "aiat_gateway",
                "capability_names": list(self.workers[worker_id].get("capability_names") or []),
            },
        }

    async def create_flow(
        self,
        *,
        name: str,
        description: str | None,
        definition_json: dict[str, Any],
        created_by: str | None = None,
        is_active: bool = False,
        version: int = 1,
    ) -> dict[str, Any]:
        self.flow = {
            "id": FLOW_ID,
            "name": name,
            "description": description,
            "version": version,
            "is_active": is_active,
            "created_by": created_by or "test-operator",
            "created_at": datetime.now(UTC),
            "definition_json": deepcopy(definition_json),
        }
        return deepcopy(self.flow)

    async def list_flows(
        self, *, is_active: bool | None = None, limit: int = 100, offset: int = 0
    ) -> list[dict[str, Any]]:
        rows = [self.flow] if self.flow is not None else []
        if is_active is not None:
            rows = [row for row in rows if row["is_active"] is is_active]
        return deepcopy(rows[offset : offset + limit])

    async def create_project(
        self,
        *,
        name: str,
        description: str | None,
        created_by: str,
        human_requester: str | None = None,
        company_id: UUID,
        config: dict[str, Any] | None = None,
        initial_context: list[dict[str, Any]] | None = None,
        project_id: UUID | None = None,
        **_kwargs: Any,
    ) -> dict[str, Any]:
        if project_id not in {None, PROJECT_ID}:
            raise AssertionError("the hermetic project store accepts only its declared test identity")
        if company_id not in self.companies:
            raise ValueError("company does not exist")
        now = datetime.now(UTC)
        self.project = {
            "id": PROJECT_ID,
            "name": name,
            "description": description,
            "state": "INIT",
            "company_id": company_id,
            "revision": 0,
            "config": deepcopy(config or {}),
            "created_by": created_by,
            "created_at": now,
            "updated_at": now,
        }
        self.project_context_items = deepcopy(initial_context or [])
        return deepcopy(self.project)

    async def list_projects(
        self, *, state: str | None = None, limit: int = 100, offset: int = 0, **_kwargs: Any
    ) -> list[dict[str, Any]]:
        rows = [self.project] if self.project is not None else []
        if state is not None:
            rows = [row for row in rows if row["state"] == state]
        return deepcopy(rows[offset : offset + limit])

    async def update_project_config(
        self, project_id: UUID, *, config: dict[str, Any]
    ) -> dict[str, Any] | None:
        if self.project is None or project_id != PROJECT_ID:
            return None
        self.project["config"] = deepcopy(config)
        self.project["revision"] += 1
        self.project["updated_at"] = datetime.now(UTC)
        return deepcopy(self.project)

    async def create_flow_instance(
        self,
        *,
        flow_id: UUID,
        flow_version: int,
        project_id: UUID,
        task_id: UUID | None = None,
        department_id: UUID | None = None,
    ) -> dict[str, Any]:
        if self.instance is not None or self.project is None or project_id != PROJECT_ID:
            raise ValueError("a persisted project without an existing flow instance is required")
        now = datetime.now(UTC)
        self.instance = {
            "id": INSTANCE_ID,
            "project_id": project_id,
            "flow_id": flow_id,
            "flow_version": flow_version,
            "status": "NOT_STARTED",
            "active_node_ids": [],
            "context_json": {},
            "created_at": now,
            "started_at": None,
            "updated_at": now,
            "retry_count": 0,
        }
        return deepcopy(self.instance)

    async def create_approval_gate(self, *, project_id: UUID, gate_type: str) -> dict[str, Any]:
        existing = next(
            (
                row
                for row in self.approval_gates
                if row.get("project_id") == project_id
                and row.get("gate_type") == gate_type
                and row.get("status") == "PENDING"
                and row.get("flow_node_execution_id") is None
            ),
            None,
        )
        if existing is not None:
            return deepcopy(existing)
        row = {
            "id": uuid4(),
            "project_id": project_id,
            "gate_type": gate_type,
            "status": "PENDING",
            "flow_node_execution_id": None,
            "created_at": datetime.now(UTC),
            "decided_by": None,
        }
        self.approval_gates.append(row)
        return deepcopy(row)

    async def decide_approval_gate(
        self,
        gate_id: UUID,
        *,
        status: str,
        decided_by: str,
        justification: str | None = None,
        human_input: dict[str, Any] | None = None,
    ) -> bool:
        gate = next((row for row in self.approval_gates if row["id"] == gate_id), None)
        if gate is None or gate["status"] != "PENDING":
            return False
        gate.update(
            {
                "status": status,
                "decided_by": decided_by,
                "justification": justification,
                "human_input": deepcopy(human_input),
                "decided_at": datetime.now(UTC),
            }
        )
        return True


class _InMemoryApprovalEngine:
    """Small SQLAlchemy connection substitute for the real pending-gate route."""

    def __init__(self, storage: _ResearchBriefStorage) -> None:
        self.storage = storage

    def connect(self) -> _InMemoryApprovalConnection:
        return _InMemoryApprovalConnection(self.storage)

    def begin(self) -> _InMemoryApprovalConnection:
        return _InMemoryApprovalConnection(self.storage)


class _InMemoryApprovalConnection:
    def __init__(self, storage: _ResearchBriefStorage) -> None:
        self.storage = storage

    async def __aenter__(self) -> _InMemoryApprovalConnection:
        return self

    async def __aexit__(self, *_exc_info: Any) -> None:
        return None

    async def execute(self, query: Any) -> _InMemoryApprovalResult:
        table = getattr(query, "table", None)
        if getattr(table, "name", None) == "companies":
            params = dict(query.compile().params)
            company_id = UUID(str(params["id"]))
            row = {
                key: params.get(key)
                for key in (
                    "id", "slug", "name", "description", "status", "created_by",
                    "created_at", "updated_at",
                )
            }
            row["id"] = company_id
            row["active_manifest_version_id"] = None
            self.storage.companies[company_id] = row
            if isinstance(self.storage, _UiCreatedResearchBriefStorage):
                self.storage.created_company_id = company_id
            return _InMemoryApprovalResult([])
        rows = [
            deepcopy(gate)
            for gate in self.storage.approval_gates
            if self.storage.project is not None
            and gate.get("project_id") == self.storage.project["id"]
            and gate.get("status") == "PENDING"
        ]
        rows.sort(key=lambda row: row["created_at"])
        return _InMemoryApprovalResult(rows)


class _InMemoryApprovalResult:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows

    def mappings(self) -> _InMemoryApprovalResult:
        return self

    def all(self) -> list[dict[str, Any]]:
        return self.rows

    def first(self) -> dict[str, Any] | None:
        return self.rows[0] if self.rows else None


class _InMemoryBlobClient:
    def __init__(self, objects: dict[tuple[str, str], bytes], *, bucket: str) -> None:
        self.objects = objects
        self.bucket = bucket
        self.client = self

    async def connect(self) -> None:
        return None

    async def upload(
        self,
        project_id: str,
        key: str,
        data: bytes,
        *,
        content_type: str,
        bucket: str | None = None,
    ):
        from mas_core.memory.blob import BlobRef

        target_bucket = bucket or self.bucket
        full_key = f"{project_id}/{key}"
        self.objects[(target_bucket, full_key)] = bytes(data)
        return BlobRef(
            bucket=target_bucket,
            key=full_key,
            sha256=hashlib.sha256(data).hexdigest(),
            size_bytes=len(data),
            content_type=content_type,
        )

    async def delete(self, reference: Any) -> None:
        self.objects.pop((reference.bucket, reference.key), None)

    async def get_object(self, *, Bucket: str, Key: str) -> dict[str, Any]:
        content = self.objects.get((Bucket, Key))
        if content is None:
            raise FileNotFoundError("synthetic object is unavailable")
        return {"Body": _InMemoryBlobBody(content), "ContentLength": len(content)}

    async def download_by_key(
        self,
        project_id: str,
        key: str,
        *,
        bucket: str | None = None,
    ) -> bytes:
        content = self.objects.get((bucket or self.bucket, f"{project_id}/{key}"))
        if content is None:
            raise FileNotFoundError("synthetic object is unavailable")
        return content

    async def close(self) -> None:
        return None


class _InMemoryBlobBody:
    def __init__(self, content: bytes) -> None:
        self.content = content
        self.offset = 0

    async def read(self, size: int = -1) -> bytes:
        if self.offset >= len(self.content):
            return b""
        end = len(self.content) if size < 0 else min(len(self.content), self.offset + size)
        chunk = self.content[self.offset : end]
        self.offset = end
        return chunk

    async def close(self) -> None:
        return None


class _FakeToolServiceClient:
    """Explicit fake Tool Service at the real orchestrator bridge boundary."""

    def __init__(self, storage: _ResearchBriefStorage, workspace_root: Path) -> None:
        self.storage = storage
        self.workspace_root = workspace_root

    async def request(self, method: str, path: str, body: dict[str, Any], *, timeout: int) -> dict[str, Any]:
        assert method == "POST"
        assert path == "/tools/file_write/run"
        assert timeout == 120
        worker_run_id = UUID(str(body["worker_run_id"]))
        run = self.storage.worker_runs[worker_run_id]
        assert str(body["project_id"]) == str(run["project_id"])
        assert str(body["caller_id"]) == str(run["worker_id"])
        assert str(body["caller_role"]).upper() == "WORKER"
        assert body["claim_owner"] == run["claim_owner"]
        assert body["claim_attempt_count"] == run["attempt_count"]
        assert body["tool_name"] == "file_write"
        assert body["permission_scope"] == ["project.write"]
        args = body["kwargs"]
        relative_path = args["path"]
        content = args["content"]
        assert isinstance(relative_path, str) and not relative_path.startswith("/")
        assert isinstance(content, str)
        run_root = (self.workspace_root / str(worker_run_id)).resolve()
        destination = (run_root / relative_path).resolve()
        assert destination.is_relative_to(run_root), "synthetic tool path escaped its run workspace"
        destination.parent.mkdir(parents=True, exist_ok=True)
        content_bytes = content.encode("utf-8")
        destination.write_bytes(content_bytes)
        self.storage.tool_dispatches.append(deepcopy(body))
        if self.storage.report_tool_outcome == "ambiguous":
            # Simulate an effect that happened before its response was lost.
            raise RuntimeError("synthetic Tool Service response was lost")
        return {
            "success": True,
            "result": {
                "success": True,
                "path": relative_path,
                "project_id": str(run["project_id"]),
                "bytes_written": len(content_bytes),
                "sha256": hashlib.sha256(content_bytes).hexdigest(),
            },
            "duration_ms": 1.0,
        }

def test_company_dispatch_grants_ignore_named_manifest_assignments() -> None:
    from fastapi import HTTPException
    from orchestrator_api import main

    class AssignmentStorage:
        def __init__(self, assignments: list[dict[str, Any]]) -> None:
            self.assignments = assignments

        async def get_project(self, project_id: UUID) -> dict[str, Any] | None:
            assert project_id == PROJECT_ID
            return {"id": PROJECT_ID, "company_id": COMPANY_ID, "state": "IN_PROGRESS"}

        async def list_company_worker_assignments(self, company_id: UUID) -> list[dict[str, Any]]:
            assert company_id == COMPANY_ID
            return deepcopy(self.assignments)

    async def verify() -> None:
        grants = {
            "company.manage": True,
            "approval_required": False,
            "status": "ACTIVE",
            "tool_grants": ["file_write"],
            "permission_grants": ["project.write"],
        }
        named_ceo = {"worker_id": "ceo", **grants}
        research_assignment = {"worker_id": str(RESEARCH_WORKER_ID), **grants}

        matched_company = await main._enforce_company_dispatch_grants(
            storage=AssignmentStorage([named_ceo, research_assignment]),
            project_id=PROJECT_ID,
            worker_id=RESEARCH_WORKER_ID,
            tool_grants=["file_write"],
            permission_requirements=["project.write"],
        )
        assert matched_company == COMPANY_ID

        try:
            await main._enforce_company_dispatch_grants(
                storage=AssignmentStorage([named_ceo]),
                project_id=PROJECT_ID,
                worker_id=RESEARCH_WORKER_ID,
                tool_grants=[],
                permission_requirements=[],
            )
        except HTTPException as exc:
            assert exc.status_code == 403
            assert exc.detail["code"] == "COMPANY_ASSIGNMENT_REQUIRED"
        else:
            raise AssertionError("a named CEO alias must not grant a UUID worker dispatch")

    asyncio.run(verify())


def _node_environment(
    api_url: str,
    node_path: str,
    dashboard_root: Path,
    *,
    operator_key: str = API_KEY,
) -> dict[str, str]:
    environment = {
        "PATH": node_path,
        "ORCHESTRATOR_URL": api_url,
        "AIAT_OPERATOR_API_KEY": operator_key,
        "JWT_SECRET": "synthetic-research-brief-dashboard-session-secret",
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
    extra_args: tuple[str, ...] = (),
    operator_key: str = API_KEY,
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
    arguments.extend(extra_args)
    completed = subprocess.run(
        arguments,
        cwd=dashboard_root,
        env=_node_environment(api_url, node_path, dashboard_root, operator_key=operator_key),
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    assert completed.returncode == 0, "BFF harness process failed"
    return json.loads(completed.stdout)


def _run_dashboard_browser(
    *,
    api_url: str,
    node_path: str,
    dashboard_root: Path,
    project_id: UUID | None,
    create_assets: bool = False,
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
    ), "the dashboard browser harness must reject non-loopback destinations"
    script = dashboard_root / "scripts" / "run-research-brief-dashboard-browser.mjs"
    command = [node_path, str(script), str(project_id or "")]
    if create_assets:
        command.append("--create-assets")
    environment = _node_environment(api_url, node_path, dashboard_root)
    working_directory: Path | None = dashboard_root

    # WSL's packaged Node may be older than the dashboard's pinned Next release.
    # When Windows Node is reachable through WSL interop, use it with converted
    # local paths and a minimal Windows-only environment instead of inheriting
    # the WSL environment or silently skipping the real browser path.
    if os.name != "nt" and Path(node_path).name.lower() in {"node", "nodejs"}:
        windows_node = shutil.which("node.exe")
        wslpath = shutil.which("wslpath")
        if windows_node and wslpath:
            version = subprocess.run(
                [node_path, "--version"], capture_output=True, text=True, timeout=5, check=False
            )
            version_text = version.stdout.strip().lstrip("v") if version.returncode == 0 else ""
            node_major = int(version_text.split(".", 1)[0]) if version_text.split(".", 1)[0].isdigit() else 0
            if node_major < 20:
                def windows_path(path: Path) -> str:
                    converted = subprocess.run(
                        [wslpath, "-w", str(path)],
                        capture_output=True,
                        text=True,
                        timeout=5,
                        check=False,
                    )
                    assert converted.returncode == 0 and converted.stdout.strip()
                    return converted.stdout.strip()

                powershell = shutil.which("powershell.exe")
                assert powershell is not None, "Windows temp directory lookup is unavailable"
                temporary_directory = subprocess.run(
                    [
                        powershell,
                        "-NoProfile",
                        "-NonInteractive",
                        "-Command",
                        "[IO.Path]::GetTempPath()",
                    ],
                    capture_output=True,
                    text=True,
                    timeout=5,
                    check=False,
                )
                assert temporary_directory.returncode == 0
                windows_temp = temporary_directory.stdout.strip().rstrip("\\/")
                windows_node_path = windows_path(Path(windows_node))
                windows_node_directory = windows_node_path.rsplit(chr(92), 1)[0]
                windows_dashboard = windows_path(dashboard_root)
                windows_script = windows_path(script)
                environment = {
                    "PATH": f"{windows_node_directory};C:\\Windows\\System32",
                    "SYSTEMROOT": "C:\\Windows",
                    "WINDIR": "C:\\Windows",
                    "TEMP": windows_temp,
                    "TMP": windows_temp,
                    "NODE_ENV": "development",
                    "NEXT_TELEMETRY_DISABLED": "1",
                    "ORCHESTRATOR_URL": api_url,
                    "AIAT_OPERATOR_API_KEY": API_KEY,
                    "JWT_SECRET": "synthetic-research-brief-dashboard-session-secret",
                    "NODE_PATH": f"{windows_dashboard}\\node_modules",
                    "WSLENV": (
                        "PATH:SYSTEMROOT:WINDIR:TEMP:TMP:NODE_ENV:NEXT_TELEMETRY_DISABLED:"
                        "ORCHESTRATOR_URL:AIAT_OPERATOR_API_KEY:JWT_SECRET:NODE_PATH"
                    ),
                }
                command = [windows_node, windows_script, str(project_id or "")]
                if create_assets:
                    command.append("--create-assets")
                working_directory = None

    completed = subprocess.run(
        command,
        cwd=working_directory,
        env=environment,
        capture_output=True,
        text=True,
        timeout=240,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr.strip() or "dashboard browser harness failed"
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


def _start_test_api(
    monkeypatch: pytest.MonkeyPatch,
    storage: _ResearchBriefStorage,
    *,
    workspace_root: Path,
):
    from orchestrator_api import artifact_content, main
    from orchestrator_api.artifact_content import make_opencode_artifact_registrar

    from mas_core.memory import blob as blob_module
    from mas_core.worker_contract import (
        AdapterContext,
        ModelMode,
        NativeWorkerAdapter,
        ToolMode,
        WorkerCapabilities,
        WorkerError,
        WorkerResult,
        WorkerToolRequest,
        WorkerUsage,
    )
    from mas_core.workflow.controller import WorkflowController

    monkeypatch.setenv("AIAT_OPERATOR_API_KEY", API_KEY)
    monkeypatch.setenv("MAS_API_KEY", "synthetic-service-key")
    monkeypatch.setenv("PM_GATEWAY_API_KEY", "synthetic-pm-key")
    monkeypatch.setenv("MINIO_ENDPOINT", "http://127.0.0.1:1")
    monkeypatch.setenv("MINIO_ACCESS_KEY", "synthetic-object-store-access")
    monkeypatch.setenv("MINIO_SECRET_KEY", "synthetic-object-store-secret")
    monkeypatch.setenv("MINIO_BUCKET", "synthetic-research-brief")
    monkeypatch.delenv("AIAT_CEO_API_KEY", raising=False)
    monkeypatch.delenv("AIAT_WORKER_API_KEY", raising=False)
    monkeypatch.delenv("GATEWAY_API_KEY", raising=False)
    monkeypatch.setenv("MAS_ENVIRONMENT", "production")

    app = main.app
    previous_runtime_caches = (
        dict(main._worker_steward_runtimes),
        dict(main._worker_adapter_runtimes),
    )
    # Each isolated API instance gets fresh process-local runtime caches. These
    # caches are not authoritative, and a prior synthetic workspace must not be
    # reused by a later browser journey in this same pytest process.
    main._worker_steward_runtimes.clear()
    main._worker_adapter_runtimes.clear()
    previous = {
        "storage": getattr(app.state, "storage", None),
        "controller": getattr(app.state, "controller", None),
        "local_host_queue_config": getattr(app.state, "local_host_queue_config", None),
        "local_host_queue_runtime": getattr(app.state, "local_host_queue_runtime", None),
        "worker_run_tasks": getattr(app.state, "worker_run_tasks", None),
    }
    app.state.storage = storage
    app.state.controller = WorkflowController(storage=storage)
    app.state.local_host_queue_config = None
    app.state.local_host_queue_runtime = None
    app.state.worker_run_tasks = set()

    workspace_root.mkdir(parents=True, exist_ok=True)

    def blob_client_factory(
        endpoint: str,
        *,
        access_key: str,
        secret_key: str,
        bucket: str,
    ) -> _InMemoryBlobClient:
        assert endpoint == "http://127.0.0.1:1"
        assert access_key == "synthetic-object-store-access"
        assert secret_key == "synthetic-object-store-secret"
        assert bucket == "synthetic-research-brief"
        return _InMemoryBlobClient(storage.blob_objects, bucket=bucket)

    monkeypatch.setattr(
        blob_module,
        "BlobClient",
        lambda endpoint, *, access_key, secret_key, bucket="mas-agents": blob_client_factory(
            endpoint,
            access_key=access_key,
            secret_key=secret_key,
            bucket=bucket,
        ),
    )

    monkeypatch.setattr(
        artifact_content,
        "new_artifact_blob_client",
        lambda **_kwargs: _InMemoryBlobClient(
            storage.blob_objects,
            bucket="synthetic-research-brief",
        ),
    )

    async def synthetic_worker(run_request: Any, runtime: Any) -> WorkerResult:
        def usage(**values: Any) -> WorkerUsage:
            if getattr(storage, "model_governed", False):
                return WorkerUsage(
                    **values,
                    provider="local-fixture",
                    exact_model_id="deterministic-fixture",
                )
            return WorkerUsage(**values)

        if run_request.task_type == "research.collect":
            return WorkerResult(
                run_id=run_request.run_id,
                worker_id=run_request.worker_id,
                success=True,
                output=deepcopy(storage.research_output),
                usage=usage(prompt_tokens=5, completion_tokens=9, duration_ms=2.0),
            )

        if run_request.task_type != "research.report":
            raise AssertionError("the hermetic Research Brief fixture dispatched an unexpected task")

        task_input = deepcopy(run_request.task_input)
        if storage.report_tool_outcome == "cancelled":
            storage.report_inputs[run_request.run_id] = task_input
            return WorkerResult(
                run_id=run_request.run_id,
                worker_id=run_request.worker_id,
                success=False,
                error=WorkerError(
                    code="CANCELLED",
                    message="synthetic adapter cancellation before report side effects",
                    terminal=True,
                    category="cancellation",
                ),
                usage=usage(prompt_tokens=1, duration_ms=1.0),
            )
        flow_context = task_input.get("flow_context")
        if not isinstance(flow_context, dict):
            raise AssertionError("the report task did not receive its declared flow context")
        storage.report_inputs[run_request.run_id] = task_input
        summary = flow_context.get("research_summary")
        sources = flow_context.get("sources")
        report_content = (
            "# Research Brief\n\n"
            f"{summary}\n\n"
            "## Sources\n"
            + "\n".join(
                f"- {source['label']} ({source['evidence_id']})"
                for source in sources
            )
            + "\n"
        )
        storage.report_contents[run_request.run_id] = report_content
        if storage.report_tool_outcome in {"success", "ambiguous"}:
            tool_request = WorkerToolRequest(
                run_id=run_request.run_id,
                tool_name="file_write",
                arguments={"path": "reports/research-brief.md", "content": report_content},
                permission_scope=["project.write"],
                idempotency_key=f"research-brief-report:{run_request.run_id}",
            )
            response = await runtime.request_tool(tool_request, timeout_seconds=5)
            storage.adapter_tool_responses[run_request.run_id] = response.model_dump(mode="json")
        # The ambiguous fixture deliberately models a runtime that reports
        # success despite a lost Tool Service response. The controller's
        # unresolved-effect barrier must keep that claim from succeeding.
        return WorkerResult(
            run_id=run_request.run_id,
            worker_id=run_request.worker_id,
            success=True,
            output={
                "report_summary": f"Research summary: {summary}",
                "research_worker_run_id": flow_context.get("worker_run_id"),
                "source_evidence_ids": [source["evidence_id"] for source in sources],
            },
            usage=usage(prompt_tokens=7, completion_tokens=12, duration_ms=3.0),
        )

    async def certified_fixture_adapter(_storage: Any, worker: dict[str, Any], **_kwargs: Any):
        context = AdapterContext(
            workspace_path=str(workspace_root),
            tool_dispatcher=main._worker_tool_dispatcher(storage, worker),
            artifact_registrar=make_opencode_artifact_registrar(
                storage,
                worker_id=UUID(str(worker["id"])),
                workspace_root=workspace_root,
                blob_client_factory=blob_client_factory,
            ),
            metadata={"test_fixture": "synthetic-in-process-research-brief"},
        )
        return NativeWorkerAdapter(
            synthetic_worker,
            worker_id=str(worker["id"]),
            capabilities=WorkerCapabilities(
                tool_mode=ToolMode.AIAT_MEDIATED,
                model_mode=(
                    ModelMode.AIAT_GATEWAY
                    if getattr(storage, "model_governed", False)
                    else ModelMode.NONE
                ),
                capability_names=list(worker.get("capability_names") or []),
            ),
            context=context,
            runtime_version="synthetic-test-adapter",
        )

    async def synthetic_project_repository_tool(
        *,
        project_id: UUID,
        operation: str,
        repository_url: str | None = None,
        branch: str | None = None,
        remote_name: str = "origin",
    ) -> dict[str, Any]:
        if repository_url is not None or operation not in {"init", "status"}:
            raise AssertionError("the hermetic project fixture supports only synthetic init and status")
        project_workspace = workspace_root / "project-workspaces" / str(project_id)
        if operation == "init":
            project_workspace.mkdir(parents=True, exist_ok=True)
        return {
            "workspace_path": str(project_workspace),
            "branch": branch or "main",
            "remote_name": remote_name,
            "initialized": True,
            "git_initialized": True,
        }

    monkeypatch.setattr(main, "_certified_worker_adapter", certified_fixture_adapter)
    monkeypatch.setattr(main, "_invoke_project_repository_tool", synthetic_project_repository_tool)
    tool_service = _FakeToolServiceClient(storage, workspace_root)
    monkeypatch.setattr(main, "_tool_service_client", lambda: tool_service)

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
        main._worker_steward_runtimes.clear()
        main._worker_steward_runtimes.update(previous_runtime_caches[0])
        main._worker_adapter_runtimes.clear()
        main._worker_adapter_runtimes.update(previous_runtime_caches[1])

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
    (
        "research_output",
        "report_tool_outcome",
        "expected_report_state",
        "expected_flow_state",
        "expected_approval_response",
        "expected_failed_evidence",
    ),
    [
        (
            {
                "research_summary": "Synthetic findings persisted by the test fixture.",
                "sources": [{"evidence_id": "source-1", "label": "Test source"}],
            },
            "success",
            "SUCCEEDED",
            "COMPLETED",
            200,
            [],
        ),
        (
            {
                "research_summary": "Synthetic findings persisted by the test fixture.",
                "sources": [{"evidence_id": "source-1", "label": "Test source"}],
            },
            "omit_artifact",
            "SUCCEEDED",
            "COMPLETED",
            200,
            ["artifacts_registered"],
        ),
        (
            {
                "research_summary": "Synthetic findings persisted by the test fixture.",
                "sources": [{"evidence_id": "source-1", "label": "Test source"}],
            },
            "ambiguous",
            "FAILED",
            "FAILED",
            200,
            ["artifacts_registered", "flow_terminal"],
        ),
        (
            {
                "research_summary": "Synthetic findings persisted by the test fixture.",
                "sources": [{"evidence_id": "source-1", "label": "Test source"}],
            },
            "cancelled",
            "CANCELLED",
            "FAILED",
            200,
            ["artifacts_registered", "flow_terminal"],
        ),
        (
            {"sources": [{"evidence_id": "source-1", "label": "Test source"}]},
            "success",
            None,
            "RUNNING",
            422,
            ["artifacts_registered", "flow_terminal", "flow_not_active"],
        ),
        (
            {
                "research_summary": ["wrong type"],
                "sources": [{"evidence_id": "source-1", "label": "Test source"}],
            },
            "success",
            None,
            "RUNNING",
            422,
            ["artifacts_registered", "flow_terminal", "flow_not_active"],
        ),
    ],
    ids=[
        "controller-mediated-report-completes-project",
        "missing-artifact-blocks-project-completion",
        "ambiguous-tool-effect-cannot-complete-project",
        "cancelled-report-cannot-complete-project",
        "missing-summary-blocks-report-admission",
        "invalid-summary-blocks-report-admission",
    ],
)
def test_research_brief_bff_controller_tool_evidence_and_project_completion(
    monkeypatch: pytest.MonkeyPatch,
    research_output: dict[str, Any],
    report_tool_outcome: str,
    expected_report_state: str | None,
    expected_flow_state: str,
    expected_approval_response: int,
    expected_failed_evidence: list[str],
    tmp_path: Path,
) -> None:
    """Drive real BFF/API/controller paths with synthetic runtime and persistence."""

    node_path, dashboard_root = _node_and_dashboard()
    storage = _ResearchBriefStorage(
        research_output=research_output,
        report_tool_outcome=report_tool_outcome,
    )
    workspace_root = tmp_path / "worker-workspace"
    api_url, stop_api = _start_test_api(monkeypatch, storage, workspace_root=workspace_root)
    try:
        started = _run_bff(
            mode="start-flow",
            api_url=api_url,
            node_path=node_path,
            dashboard_root=dashboard_root,
        )
        assert started["status"] == 200, started
        assert started["body"]["status"] == "RUNNING"

        start_settlement = _run_bff(
            mode="complete-start",
            api_url=api_url,
            node_path=node_path,
            dashboard_root=dashboard_root,
        )
        assert start_settlement["status"] == 200, start_settlement
        assert storage.instance["active_node_ids"] == ["research"]

        # Completing the start control node activates and dispatches the
        # governed research task through the real flow transition handler.
        assert len(storage.admissions) == 1
        research_run = storage.admissions[0]
        assert research_run["task_type"] == "research.collect"
        assert research_run["request_json"]["task_input"] == {"query": "AIAT task input contract"}
        research_run_id = UUID(str(research_run["id"]))
        research_terminal = _run_bff(
            mode="wait-run",
            api_url=api_url,
            node_path=node_path,
            dashboard_root=dashboard_root,
            run_id=research_run_id,
        )
        assert research_terminal["status"] == 200, research_terminal
        assert research_terminal["body"]["state"] == "SUCCEEDED"
        assert research_terminal["body"]["result_json"]["output"] == research_output

        completion = _run_bff(
            mode="complete-research",
            api_url=api_url,
            node_path=node_path,
            dashboard_root=dashboard_root,
            run_id=research_run_id,
        )
        assert completion["status"] == 200, completion
        assert storage.instance["status"] == "RUNNING"
        assert storage.instance["active_node_ids"] == ["review"]

        instance_read = _run_bff(
            mode="read-instance",
            api_url=api_url,
            node_path=node_path,
            dashboard_root=dashboard_root,
        )
        assert instance_read["status"] == 200, instance_read
        persisted_context = instance_read["body"]["context_json"]
        assert persisted_context.get("research_summary") == research_output.get("research_summary")
        assert persisted_context.get("worker_run_id") == str(research_run_id)

        approval = _run_bff(
            mode="approve-review",
            api_url=api_url,
            node_path=node_path,
            dashboard_root=dashboard_root,
        )
        assert approval["status"] == expected_approval_response, approval
        gate = storage.approval_gates[0]
        assert gate["status"] == "APPROVED"
        assert gate["decided_by"] == "operator"

        if expected_report_state is not None:
            assert len(storage.admissions) == 2
            report_run = storage.admissions[1]
            report_run_id = UUID(str(report_run["id"]))
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
            report_terminal = _run_bff(
                mode="wait-run",
                api_url=api_url,
                node_path=node_path,
                dashboard_root=dashboard_root,
                run_id=report_run_id,
            )
            assert report_terminal["status"] == 200, report_terminal
            assert report_terminal["body"]["state"] == expected_report_state
            assert storage.report_inputs[report_run_id] == report_input
            assert len(storage.worker_usage[report_run_id]) == (
                0 if report_tool_outcome == "cancelled" else 1
            )

            tool_effects = _run_bff(
                mode="read-run-resource",
                api_url=api_url,
                node_path=node_path,
                dashboard_root=dashboard_root,
                run_id=report_run_id,
                extra_args=("tool-effects",),
            )
            assert tool_effects["status"] == 200, tool_effects
            effect_rows = tool_effects["body"]["tool_effects"]
            if report_tool_outcome == "success":
                assert len(effect_rows) == 1
                assert effect_rows[0]["state"] == "COMPLETED"
                assert storage.adapter_tool_responses[report_run_id]["success"] is True
            elif report_tool_outcome == "ambiguous":
                assert len(effect_rows) == 1
                assert effect_rows[0]["state"] == "AMBIGUOUS"
                assert report_terminal["body"]["error_json"]["code"] == "WORKER_TOOL_EFFECT_UNRESOLVED"
                assert storage.adapter_tool_responses[report_run_id]["success"] is False
            else:
                assert effect_rows == []

            run_artifacts = _run_bff(
                mode="read-run-resource",
                api_url=api_url,
                node_path=node_path,
                dashboard_root=dashboard_root,
                run_id=report_run_id,
                extra_args=("artifacts",),
            )
            assert run_artifacts["status"] == 200, run_artifacts
            assert len(run_artifacts["body"]) == (1 if report_tool_outcome == "success" else 0)
            run_usage = _run_bff(
                mode="read-run-resource",
                api_url=api_url,
                node_path=node_path,
                dashboard_root=dashboard_root,
                run_id=report_run_id,
                extra_args=("usage",),
            )
            assert run_usage["status"] == 200, run_usage
            assert len(run_usage["body"]) == (0 if report_tool_outcome == "cancelled" else 1)

            report_settlement = _run_bff(
                mode="complete-report" if expected_report_state == "SUCCEEDED" else "fail-report",
                api_url=api_url,
                node_path=node_path,
                dashboard_root=dashboard_root,
                run_id=report_run_id,
            )
            assert report_settlement["status"] == 200, report_settlement
            assert storage.instance["status"] == expected_flow_state
        else:
            assert len(storage.admissions) == 1
            assert [row["task_type"] for row in storage.admissions] == ["research.collect"]
            assert persisted_context.get("active_worker_runs", {}).get("report") is None
            assert any(
                row["node_id"] == "report" and row["status"] == "RUNNING"
                for row in storage.executions
            )

        evidence_before_completion = _run_bff(
            mode="read-project-evidence",
            api_url=api_url,
            node_path=node_path,
            dashboard_root=dashboard_root,
        )
        assert evidence_before_completion["status"] == 200, evidence_before_completion
        if not expected_failed_evidence:
            check_results = {
                check["name"]: check["passed"]
                for check in evidence_before_completion["body"]["checks"]
            }
            assert check_results["flow_terminal"] is True
            assert check_results["artifacts_registered"] is (report_tool_outcome == "success")
        else:
            assert evidence_before_completion["body"]["status"] == "incomplete"

        project_artifacts = _run_bff(
            mode="read-project-artifacts",
            api_url=api_url,
            node_path=node_path,
            dashboard_root=dashboard_root,
        )
        assert project_artifacts["status"] == 200, project_artifacts
        expected_artifact_count = (
            1
            if expected_report_state == "SUCCEEDED" and report_tool_outcome == "success"
            else 0
        )
        assert len(project_artifacts["body"]) == expected_artifact_count
        if expected_artifact_count:
            artifact = project_artifacts["body"][0]
            assert artifact["metadata"]["project_id"] == str(PROJECT_ID)
            assert artifact["metadata"]["worker_run_id"] == str(storage.admissions[1]["id"])
            blob_key = (artifact["metadata"]["blob_bucket"], artifact["metadata"]["blob_key"])
            assert storage.blob_objects[blob_key].decode("utf-8") == storage.report_contents[UUID(str(storage.admissions[1]["id"]))]
            assert hashlib.sha256(storage.blob_objects[blob_key]).hexdigest() == artifact["sha256"]

        for event in ("all_sprints_done", "retrospective_done"):
            transition = _run_bff(
                mode="transition-project",
                api_url=api_url,
                node_path=node_path,
                dashboard_root=dashboard_root,
                extra_args=(event,),
            )
            assert transition["status"] == 200, transition
        completion = _run_bff(
            mode="transition-project",
            api_url=api_url,
            node_path=node_path,
            dashboard_root=dashboard_root,
            extra_args=("kpi_saved",),
        )
        if expected_failed_evidence:
            assert completion["status"] == 409, completion
            assert completion["body"]["detail"]["code"] == "PROJECT_EVIDENCE_INCOMPLETE"
            failed_names = completion["body"]["detail"]["failed_check_names"]
            assert sorted(expected_failed_evidence) == sorted(failed_names)
        else:
            assert completion["status"] == 200, completion

        project = _run_bff(
            mode="read-project",
            api_url=api_url,
            node_path=node_path,
            dashboard_root=dashboard_root,
        )
        assert project["status"] == 200, project
        assert project["body"]["state"] == ("KPI_PERSISTENCE" if expected_failed_evidence else "COMPLETED")
        final_evidence = _run_bff(
            mode="read-project-evidence",
            api_url=api_url,
            node_path=node_path,
            dashboard_root=dashboard_root,
        )
        assert final_evidence["status"] == 200, final_evidence
        if expected_failed_evidence:
            assert final_evidence["body"]["status"] == "incomplete"
            final_failed_names = sorted(
                check["name"]
                for check in final_evidence["body"]["checks"]
                if check["required"] and not check["passed"]
            )
            assert final_failed_names == sorted(expected_failed_evidence)
        else:
            assert final_evidence["body"]["status"] == "complete"
    finally:
        stop_api()


def test_research_brief_bff_cancellation_and_flow_retry_recover_authoritative_state(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Operator cancellation, denied actions, and flow retry use persisted controller state."""

    node_path, dashboard_root = _node_and_dashboard()
    storage = _ResearchBriefStorage(
        research_output={
            "research_summary": "Synthetic findings persisted by the test fixture.",
            "sources": [{"evidence_id": "source-1", "label": "Synthetic source"}],
        },
        require_report_document=True,
        include_legacy_documents=True,
    )
    workspace_root = tmp_path / "cancel-retry-worker-workspace"
    api_url, stop_api = _start_test_api(monkeypatch, storage, workspace_root=workspace_root)

    def call(
        mode: str,
        *,
        run_id: UUID | None = None,
        extra_args: tuple[str, ...] = (),
        operator_key: str = API_KEY,
    ) -> dict[str, Any]:
        return _run_bff(
            mode=mode,
            api_url=api_url,
            node_path=node_path,
            dashboard_root=dashboard_root,
            run_id=run_id,
            extra_args=extra_args,
            operator_key=operator_key,
        )

    try:
        started = call("start-flow")
        assert started["status"] == 200
        start_settlement = call("complete-start")
        assert start_settlement["status"] == 200
        research_run_id = UUID(str(storage.admissions[0]["id"]))
        research_terminal = call("wait-run", run_id=research_run_id)
        assert research_terminal["status"] == 200
        assert research_terminal["body"]["state"] == "SUCCEEDED"
        assert call("complete-research", run_id=research_run_id)["status"] == 200
        assert storage.instance["active_node_ids"] == ["review"]

        # Holding the report in the existing host queue keeps this operator
        # action deterministic without starting an adapter or worker process.
        from orchestrator_api import main as api_main

        assert api_main.app.state.storage is storage
        api_main.app.state.local_host_queue_config = object()
        assert call("approve-review")["status"] == 200
        assert len(storage.admissions) == 2
        cancelled_report = storage.admissions[1]
        cancelled_run_id = UUID(str(cancelled_report["id"]))
        assert cancelled_report["task_type"] == "research.report"

        queued_status = call("read-run", run_id=cancelled_run_id)
        assert queued_status["status"] == 200
        assert queued_status["body"]["state"] == "QUEUED"

        denied_cancel = call(
            "cancel-run",
            run_id=cancelled_run_id,
            operator_key="synthetic-invalid-operator-key",
        )
        assert denied_cancel["status"] == 401, denied_cancel
        assert storage.worker_runs[cancelled_run_id]["state"] == "QUEUED"
        assert storage.worker_runs[cancelled_run_id]["cancel_requested_at"] is None
        assert storage.tool_dispatches == []

        cancelled = call("cancel-run", run_id=cancelled_run_id)
        assert cancelled["status"] == 200
        assert cancelled["body"]["state"] == "CANCELLED"
        repeated_cancel = call("cancel-run", run_id=cancelled_run_id)
        assert repeated_cancel["status"] == 200
        assert repeated_cancel["body"]["state"] == "CANCELLED"
        assert storage.worker_runs[cancelled_run_id]["state"] == "CANCELLED"

        failed_flow = call("fail-report", run_id=cancelled_run_id)
        assert failed_flow["status"] == 200
        assert failed_flow["body"]["status"] == "FAILED"
        assert storage.instance["active_node_ids"] == []
        assert storage.worker_runs[cancelled_run_id]["state"] == "CANCELLED"
        assert storage.worker_tool_effects == {}
        assert storage.worker_artifacts[cancelled_run_id] == []
        assert storage.worker_usage[cancelled_run_id] == []
        assert storage.project["state"] == "IN_PROGRESS"

        incomplete = call("read-project-evidence")
        assert incomplete["status"] == 200
        assert incomplete["body"]["status"] == "incomplete"
        failed_checks = {
            check["name"]
            for check in incomplete["body"]["checks"]
            if check["required"] and not check["passed"]
        }
        assert {"flow_terminal", "artifacts_registered"} <= failed_checks

        assert call("transition-project", extra_args=("all_sprints_done",))["status"] == 200
        assert call("transition-project", extra_args=("retrospective_done",))["status"] == 200
        blocked_completion = call("transition-project", extra_args=("kpi_saved",))
        assert blocked_completion["status"] == 409
        assert blocked_completion["body"]["detail"]["code"] == "PROJECT_EVIDENCE_INCOMPLETE"
        assert storage.project["state"] == "KPI_PERSISTENCE"

        retried_flow = call("retry-flow")
        assert retried_flow["status"] == 200, retried_flow
        assert retried_flow["body"]["status"] == "RUNNING"
        assert retried_flow["body"]["retry_count"] == 1
        assert retried_flow["body"]["active_node_ids"] == ["review"]
        assert len(storage.admissions) == 2

        duplicate_retry = call("retry-flow")
        assert duplicate_retry["status"] == 409
        assert len(storage.admissions) == 2

        # After the retry's fresh review decision, the normal asynchronous
        # controller path executes and persists a new report attempt.
        api_main.app.state.local_host_queue_config = None
        assert call("approve-review")["status"] == 200
        assert len(storage.admissions) == 3
        recovered_report_id = UUID(str(storage.admissions[2]["id"]))
        assert recovered_report_id != cancelled_run_id
        recovered_terminal = call("wait-run", run_id=recovered_report_id)
        assert recovered_terminal["status"] == 200
        assert recovered_terminal["body"]["state"] == "SUCCEEDED"
        assert call("complete-report", run_id=recovered_report_id)["status"] == 200
        assert storage.instance["status"] == "COMPLETED"

        registered_document = call(
            "register-report-document",
            extra_args=(str(storage.artifacts[0]["id"]),),
        )
        assert registered_document["status"] == 201
        assert registered_document["body"]["doc_type"] == "RESEARCH_BRIEF"
        assert registered_document["body"]["project_id"] == str(PROJECT_ID)
        assert registered_document["body"]["blob_sha256"] == storage.artifacts[0]["sha256"]

        recovered_evidence = call("read-project-evidence")
        assert recovered_evidence["status"] == 200
        failed_recovered_checks = [
            check["name"]
            for check in recovered_evidence["body"]["checks"]
            if check["required"] and not check["passed"]
        ]
        assert recovered_evidence["body"]["status"] == "complete", failed_recovered_checks
        assert all(row["state"] in {"SUCCEEDED", "CANCELLED"} for row in storage.worker_runs.values())
        assert storage.worker_runs[cancelled_run_id]["state"] == "CANCELLED"
        assert storage.worker_runs[recovered_report_id]["state"] == "SUCCEEDED"
        assert len(storage.artifacts) == 1
        assert storage.artifacts[0]["metadata"]["worker_run_id"] == str(recovered_report_id)
        assert len(storage.documents) == 3  # two retained legacy documents plus one report

        final_transition = call("transition-project", extra_args=("kpi_saved",))
        assert final_transition["status"] == 200
        assert storage.project["state"] == "COMPLETED"
        final_project = call("read-project")
        assert final_project["status"] == 200
        assert final_project["body"]["state"] == "COMPLETED"
    finally:
        stop_api()


def test_research_brief_dashboard_browser_uses_real_bff_and_verified_project_evidence(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Exercise the actual dashboard against the hermetic API/controller fixture."""

    node_path, dashboard_root = _node_and_dashboard()
    if not (dashboard_root / "node_modules" / "playwright").exists():
        pytest.skip("dashboard Playwright runtime is not installed")
    storage = _ResearchBriefStorage(
        research_output={
            "research_summary": "Synthetic findings persisted by the test fixture.",
            "sources": [{"evidence_id": "source-1", "label": "Synthetic source"}],
        },
        require_report_document=True,
        include_legacy_documents=True,
    )
    workspace_root = tmp_path / "browser-worker-workspace"
    api_url, stop_api = _start_test_api(monkeypatch, storage, workspace_root=workspace_root)
    try:
        browser_result = _run_dashboard_browser(
            api_url=api_url,
            node_path=node_path,
            dashboard_root=dashboard_root,
            project_id=PROJECT_ID,
        )

        assert browser_result["project_id"] == str(PROJECT_ID)
        assert browser_result["project_status"] == "COMPLETED"
        assert browser_result["flow_status"] == "COMPLETED"
        assert browser_result["evidence_status"] == "complete"
        assert len(storage.admissions) == 2
        assert [row["task_type"] for row in storage.admissions] == [
            "research.collect",
            "research.report",
        ]
        assert all(row["state"] == "SUCCEEDED" for row in storage.worker_runs.values())

        report_run_id = UUID(str(browser_result["report_run_id"]))
        report_run = storage.worker_runs[report_run_id]
        assert report_run["project_id"] == PROJECT_ID
        assert storage.report_inputs[report_run_id]["flow_context"]["worker_run_id"] == str(
            browser_result["research_run_id"]
        )
        effects = storage.worker_tool_effects[
            (report_run_id, f"research-brief-report:{report_run_id}")
        ]
        assert effects["state"] == "COMPLETED"
        assert len(storage.artifacts) == 1
        artifact = storage.artifacts[0]
        metadata = artifact["metadata"]
        assert metadata["project_id"] == str(PROJECT_ID)
        assert metadata["worker_run_id"] == str(report_run_id)
        assert metadata["worker_run_attempt"] == browser_result["artifact_attempt"]
        assert metadata["blob_key"] == (
            f"{PROJECT_ID}/worker-runs/{report_run_id}/"
            f"attempt-{metadata['worker_run_attempt']}/reports/research-brief.md"
        )
        assert artifact["sha256"] == browser_result["artifact_sha256"]
        document = next(row for row in storage.documents if row["doc_type"] == "RESEARCH_BRIEF")
        assert document["project_id"] == PROJECT_ID
        assert document["doc_type"] == "RESEARCH_BRIEF"
        assert document["blob_key"] == metadata["blob_key"]
        assert document["blob_sha256"] == artifact["sha256"]

        evidence = _run_bff(
            mode="read-project-evidence",
            api_url=api_url,
            node_path=node_path,
            dashboard_root=dashboard_root,
        )
        assert evidence["status"] == 200
        assert evidence["body"]["status"] == "complete"
        checks = {check["name"]: check["passed"] for check in evidence["body"]["checks"]}
        assert checks["documents_exist"] is True
        assert checks["required_document_types"] is True
        assert checks["document_bodies_retrievable"] is True
        assert checks["artifacts_registered"] is True
        assert checks["flow_terminal"] is True
    finally:
        stop_api()


def test_research_brief_dashboard_creates_company_bound_flow_and_project(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Create the company, manifest, active flow, and project through real UI/API routes."""

    node_path, dashboard_root = _node_and_dashboard()
    if not (dashboard_root / "node_modules" / "playwright").exists():
        pytest.skip("dashboard Playwright runtime is not installed")
    storage = _UiCreatedResearchBriefStorage(
        research_output={
            "research_summary": "Synthetic findings persisted by the test fixture.",
            "sources": [{"evidence_id": "source-1", "label": "Synthetic source"}],
        }
    )
    workspace_root = tmp_path / "browser-worker-workspace"
    api_url, stop_api = _start_test_api(monkeypatch, storage, workspace_root=workspace_root)
    try:
        try:
            browser_result = _run_dashboard_browser(
                api_url=api_url,
                node_path=node_path,
                dashboard_root=dashboard_root,
                project_id=None,
                create_assets=True,
            )
        except AssertionError as exc:
            execution_summary = [
                {
                    "node_id": row.get("node_id"),
                    "status": row.get("status"),
                    "error_code": (row.get("error") or {}).get("code")
                    if isinstance(row.get("error"), dict)
                    else None,
                }
                for row in storage.executions
            ]
            run_summary = [
                {
                    "task_type": row.get("task_type"),
                    "state": row.get("state"),
                    "error_code": (row.get("error_json") or {}).get("code")
                    if isinstance(row.get("error_json"), dict)
                    else None,
                }
                for row in storage.worker_runs.values()
            ]
            synthetic_state = {
                "project_state": (storage.project or {}).get("state"),
                "flow_instance_status": (storage.instance or {}).get("status"),
                "active_node_ids": (storage.instance or {}).get("active_node_ids"),
                "company_assignments": len(
                    ((storage.company_manifest or {}).get("manifest_json") or {}).get("worker_assignments", [])
                ),
                "executions": execution_summary,
                "runs": run_summary,
            }
            raise AssertionError(
                f"{exc}; synthetic_state={json.dumps(synthetic_state, sort_keys=True)}"
            ) from exc

        company_id = UUID(str(browser_result["company_id"]))
        assert storage.created_company_id == company_id
        assert storage.companies[company_id]["slug"] == "hermetic-research-brief"
        assert storage.company_manifest is not None
        assert storage.company_manifest["status"] == "ACTIVE"
        assert storage.company_manifest["manifest_version"] == 1
        assignments = storage.company_manifest["manifest_json"]["worker_assignments"]
        assert {row["worker_id"] for row in assignments if row["status"] == "ACTIVE"} >= {
            str(RESEARCH_WORKER_ID),
            str(REPORT_WORKER_ID),
        }

        assert storage.flow is not None
        assert storage.flow["is_active"] is True
        task_nodes = [
            node for node in storage.flow["definition_json"]["nodes"] if node["type"] == "task"
        ]
        task_bindings = {node["config"]["task_type"]: node["config"] for node in task_nodes}
        assert task_bindings["research.collect"]["worker_id"] == str(RESEARCH_WORKER_ID)
        assert task_bindings["research.report"]["worker_id"] == str(REPORT_WORKER_ID)
        assert task_bindings["research.collect"]["model_profile_id"] == MODEL_PROFILE_ID
        assert task_bindings["research.report"]["model_profile_id"] == MODEL_PROFILE_ID

        assert storage.project is not None
        assert storage.project["id"] == PROJECT_ID
        assert storage.project["company_id"] == company_id
        company_policy = storage.company_manifest["manifest_json"]["evidence_policy"]["default_policy"]
        assert storage.project["config"]["evidence_policy"] == {
            "policy_id": company_policy["policy_id"],
            "version": company_policy["version"],
            "requirements": company_policy["requirements"],
        }
        assert browser_result["project_id"] == str(PROJECT_ID)
        assert browser_result["project_status"] == "COMPLETED"
        assert browser_result["flow_status"] == "COMPLETED"
        assert browser_result["evidence_status"] == "complete"
        assert browser_result["assignment_gate"] == {
            "company_id": str(company_id),
            "status": 403,
            "blocker": "COMPANY_ASSIGNMENT_REQUIRED",
        }

        # The pre-manifest probe was rejected before admission and before any
        # model-resolution snapshot. Only the two authorized flow tasks ran.
        assert [row["task_type"] for row in storage.admissions] == [
            "research.collect",
            "research.report",
        ]
        assert len(storage.model_snapshots) == 2
        assert all(row["state"] == "SUCCEEDED" for row in storage.worker_runs.values())

        report_run_id = UUID(str(browser_result["report_run_id"]))
        report_run = storage.worker_runs[report_run_id]
        assert report_run["project_id"] == PROJECT_ID
        report_input = storage.report_inputs[report_run_id]
        assert report_input["flow_context"]["worker_run_id"] == str(browser_result["research_run_id"])
        assert report_input["flow_context"]["research_summary"] == storage.research_output["research_summary"]
        assert browser_result["document_id"]
        assert browser_result["artifact_sha256"]
    finally:
        stop_api()

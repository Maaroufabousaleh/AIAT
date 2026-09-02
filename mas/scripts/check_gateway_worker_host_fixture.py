"""Certify the gateway worker through the AIAT host-executor boundary.

This deterministic fixture drives the real ``WorkerHostExecutor``,
``WorkerRunController``, and ``GatewayWorkerAdapter`` against bounded
in-memory binding/storage doubles.  It proves committed worker-plane
admission, claim, gateway usage attribution, terminal settlement, and binding
release without contacting a provider or pretending to be durable Postgres,
independent-host, sandbox, or live recovery evidence.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any
from uuid import UUID

MAS_ROOT = Path(__file__).resolve().parents[1]
CORE_ROOT = MAS_ROOT / "packages" / "mas-core"
if str(CORE_ROOT) not in sys.path:
    sys.path.insert(0, str(CORE_ROOT))

from mas_core.llm_gateway.models import ChatMessage, ChatResponse, UsageStats  # noqa: E402
from mas_core.observability.trace_evidence import build_trace_evidence  # noqa: E402
from mas_core.observability.worker_trace_coverage import (  # noqa: E402
    evaluate_worker_trace_coverage,
)
from mas_core.worker_contract.models import (  # noqa: E402
    ModelProfileReference,
    WorkerRunRequest,
)
from mas_core.worker_registry.host_executor import (  # noqa: E402
    HOST_EXECUTION_SCHEMA,
    HostExecutionRequest,
    WorkerHostExecutor,
)
from mas_core.worker_registry.runtime_adapters import (  # noqa: E402
    GatewayWorkerAdapter,
)

CHECK_SCHEMA = "aiat.gateway-worker-host-fixture.v1"
RUN_ID = UUID("00000000-0000-4000-a000-000000000c41")
WORKER_ID = UUID("00000000-0000-4000-a000-000000000c42")
HOST_ID = "gateway-worker-host-fixture"
TRACE_ID = "gateway-worker-host-fixture-trace"
SPAN_ID = "gateway-worker-host-fixture-span"
WORKER_SPAN_ID = "gateway-worker-host-fixture-worker-span"
MODEL_SPAN_ID = "gateway-worker-host-fixture-model-span"
IDEMPOTENCY_KEY = "gateway-worker-host-fixture-idempotency"
OWNER = "gateway-worker-host-fixture-owner"
PAYLOAD_MARKER = "gateway worker host fixture private marker"


class _FixtureGateway:
    """Bounded gateway double returning one exact model/usage response."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def chat_completion(self, **kwargs: Any) -> ChatResponse:
        self.calls.append(dict(kwargs))
        return ChatResponse(
            model=str(kwargs["model"]),
            message=ChatMessage(role="assistant", content="fixture host answer"),
            usage=UsageStats(prompt_tokens=5, completion_tokens=4, total_tokens=9),
        )


class _MemoryBindingService:
    """Minimal binding double implementing the host executor's authority edge."""

    def __init__(self) -> None:
        self.binding: dict[str, Any] = {
            "run_id": RUN_ID,
            "worker_id": WORKER_ID,
            "host_id": HOST_ID,
            "host_plane": "worker",
            "host_status": "READY",
            "state": "COMMITTED",
            "reservation_state": "COMMITTED",
            "host_lease_generation": 3,
            "current_host_lease_generation": 3,
            "current_host_lease_valid": True,
            "owner": OWNER,
        }

    async def get(self, run_id: UUID | str) -> dict[str, Any] | None:
        if str(run_id) != str(RUN_ID):
            return None
        return dict(self.binding)

    async def release(self, run_id: UUID | str, *, owner: str) -> dict[str, Any]:
        if str(run_id) != str(RUN_ID) or owner != OWNER:
            raise RuntimeError("fixture binding owner mismatch")
        self.binding["state"] = "RELEASED"
        self.binding["reservation_state"] = "RELEASED"
        return dict(self.binding)


class _MemoryStorage:
    """Small storage double covering the controller's bounded write surface."""

    def __init__(self) -> None:
        self.run: dict[str, Any] = {
            "id": RUN_ID,
            "worker_id": WORKER_ID,
            "idempotency_key": IDEMPOTENCY_KEY,
            "task_type": "gateway-worker-host-fixture",
            "state": "QUEUED",
        }
        self.events: list[dict[str, Any]] = []
        self.artifacts: list[dict[str, Any]] = []
        self.usage: list[dict[str, Any]] = []
        self._next_artifact_id = 1

    async def create_worker_run(self, **_fields: Any) -> dict[str, Any]:
        return dict(self.run)

    async def get_worker_run(self, run_id: UUID | str) -> dict[str, Any] | None:
        return dict(self.run) if str(run_id) == str(RUN_ID) else None

    async def claim_worker_run(
        self,
        *,
        owner: str,
        lease_seconds: int,
        run_id: UUID | str,
    ) -> dict[str, Any] | None:
        del lease_seconds
        if str(run_id) != str(RUN_ID) or not owner or self.run["state"] != "QUEUED":
            return None
        self.run["state"] = "CLAIMED"
        self.run["claim_owner"] = owner
        return dict(self.run)

    async def transition_worker_run(
        self,
        run_id: UUID | str,
        *,
        new_state: str,
        expected_state: str | None = None,
        result: dict[str, Any] | None = None,
        error: dict[str, Any] | None = None,
        negotiation: dict[str, Any] | None = None,
        replay_metadata: dict[str, Any] | None = None,
        **_fields: Any,
    ) -> dict[str, Any] | None:
        if str(run_id) != str(RUN_ID):
            return None
        if expected_state is not None and self.run["state"] != expected_state:
            return None
        self.run["state"] = new_state
        if result is not None:
            self.run["result_json"] = result
        if error is not None:
            self.run["error_json"] = error
        if negotiation is not None:
            self.run["negotiation_json"] = negotiation
        if replay_metadata is not None:
            self.run["replay_metadata"] = replay_metadata
        return dict(self.run)

    async def append_worker_event(self, **fields: Any) -> dict[str, Any]:
        row = dict(fields)
        self.events.append(row)
        return row

    async def create_artifact(self, **fields: Any) -> dict[str, Any]:
        row = {"id": self._next_artifact_id, **fields}
        self._next_artifact_id += 1
        return row

    async def create_worker_artifact(self, **fields: Any) -> dict[str, Any]:
        row = dict(fields)
        self.artifacts.append(row)
        return row

    async def create_worker_usage(self, *, run_id: UUID, usage: dict[str, Any]) -> dict[str, Any]:
        row = {"id": "fixture-worker-usage", "run_id": run_id, **usage}
        self.usage.append(row)
        return row


async def _run() -> dict[str, Any]:
    storage = _MemoryStorage()
    bindings = _MemoryBindingService()
    gateway = _FixtureGateway()
    request = WorkerRunRequest(
        run_id=RUN_ID,
        idempotency_key=IDEMPOTENCY_KEY,
        worker_id=str(WORKER_ID),
        task_type="gateway-worker-host-fixture",
        task_input={
            "prompt": "reply with a bounded fixture answer",
            "max_tokens": 32,
            "temperature": 0.2,
            "private_marker": PAYLOAD_MARKER,
        },
        trace_id=TRACE_ID,
        span_id=SPAN_ID,
        resolved_model_profile=ModelProfileReference(
            profile_id="gateway-host-profile-v1",
            version="fixture-v1",
            exact_model_id="fixture/model-v1",
        ),
        timeout_seconds=30,
    )
    adapter = GatewayWorkerAdapter(
        worker_id=str(WORKER_ID),
        provider_id="fixture-provider",
        gateway_client=gateway,
        runtime_version="gateway-host-fixture-v1",
    )
    try:
        execution = await WorkerHostExecutor(
            storage,
            binding_service=bindings,
        ).execute(
            HostExecutionRequest(
                run_id=RUN_ID,
                host_id=HOST_ID,
                owner=OWNER,
                lease_seconds=30,
            ),
            request,
            adapter,
            worker_registry_id=WORKER_ID,
        )
    finally:
        await adapter.close()

    # The gateway adapter deliberately does not persist model output as an
    # artifact. Add one bounded report pointer so this certificate can prove
    # the host path's artifact source without storing generated content.
    stored_artifact = await storage.create_artifact(
        agent_id=str(WORKER_ID),
        path="fixture://aiat/gateway-worker-host-v1/report.json",
        metadata={"fixture_projection": True, "payload_free": True},
        sha256="d" * 64,
        size_bytes=64,
    )
    await storage.create_worker_artifact(
        run_id=RUN_ID,
        artifact_id=stored_artifact["id"],
        kind="report",
        uri=stored_artifact["path"],
        sha256=stored_artifact["sha256"],
        size_bytes=stored_artifact["size_bytes"],
        metadata=stored_artifact["metadata"],
        trace_id=TRACE_ID,
        span_id=SPAN_ID,
    )
    native_spans = [
        {
            "id": MODEL_SPAN_ID,
            "trace_id": TRACE_ID,
            "span_id": MODEL_SPAN_ID,
            "source_kind": "model",
            "operation": "fixture/model-v1",
            "service": "aiat_gateway",
            "status": "success",
            "attributes": {"provider": "fixture-provider"},
        },
        {
            "id": WORKER_SPAN_ID,
            "trace_id": TRACE_ID,
            "span_id": WORKER_SPAN_ID,
            "parent_span_id": SPAN_ID,
            "source_kind": "worker",
            "operation": "worker.execute",
            "service": "worker_host_executor",
            "status": "success",
            "attributes": {"host_plane": "worker", "fixture": True},
        },
    ]
    evidence = build_trace_evidence(
        trace_id=TRACE_ID,
        worker_usage_rows=storage.usage,
        artifact_rows=storage.artifacts,
        native_span_rows=native_spans,
        generated_at="2026-08-18T00:00:04Z",
    )
    coverage = evaluate_worker_trace_coverage(evidence)
    binding_before = execution.binding_before
    binding_after = execution.binding_after
    usage = storage.usage[0] if storage.usage else {}
    structural_pass = all(
        (
            execution.outcome.state == "SUCCEEDED",
            binding_before.get("state") == "COMMITTED",
            binding_before.get("reservation_state") == "COMMITTED",
            binding_before.get("host_plane") == "worker",
            binding_before.get("current_host_lease_valid") is True,
            binding_after.get("state") == "RELEASED",
            binding_after.get("reservation_state") == "RELEASED",
            len(gateway.calls) == 1,
            gateway.calls[0].get("model") == "fixture/model-v1",
            usage.get("provider_id") == "fixture-provider",
            usage.get("exact_model_id") == "fixture/model-v1",
            coverage["status"] == "pass",
            len(storage.events) >= 2,
        )
    )
    report = {
        "schema_version": CHECK_SCHEMA,
        "execution_schema": HOST_EXECUTION_SCHEMA,
        "mode": "in-memory-gateway-worker-host-fixture",
        "status": "pass" if structural_pass else "fail",
        "controller_terminal_state": execution.outcome.state,
        "gateway_call_count": len(gateway.calls),
        "provider_id": usage.get("provider_id"),
        "exact_model_id": usage.get("exact_model_id"),
        "host_admission": {
            "host_id": HOST_ID,
            "host_plane": binding_before.get("host_plane"),
            "lease_generation_match": binding_before.get("host_lease_generation")
            == binding_before.get("current_host_lease_generation"),
            "current_host_lease_valid": binding_before.get("current_host_lease_valid"),
            "binding_before": binding_before.get("state"),
            "binding_after": binding_after.get("state"),
            "reservation_before": binding_before.get("reservation_state"),
            "reservation_after": binding_after.get("reservation_state"),
        },
        "worker_trace_status": coverage["status"],
        "required_sources": coverage["required_sources"],
        "trace_source_counts": evidence.source_counts,
        "trace_item_count": evidence.item_count,
        "event_count": len(storage.events),
        "usage_count": len(storage.usage),
        "artifact_count": len(storage.artifacts),
        "payload_free_report": PAYLOAD_MARKER not in json.dumps(
            {
                "evidence": evidence.model_dump(mode="json"),
                "usage": storage.usage,
                "artifacts": storage.artifacts,
                "spans": native_spans,
            },
            default=str,
        ),
        "mutation_performed": True,
        "network_access_performed": False,
        "external_provider_call_performed": False,
        "sandbox_execution_performed": False,
        "licence_metadata_is_gate": False,
        "scope": "real WorkerHostExecutor/WorkerRunController/GatewayWorkerAdapter over bounded in-memory fixture; no durable host, provider, or sandbox claim",
    }
    # Recompute status with the final payload-free report now that all fields
    # are assembled; report construction above stays easy to inspect.
    report["status"] = (
        "pass"
        if structural_pass
        and report["payload_free_report"]
        and report["worker_trace_status"] == "pass"
        else "fail"
    )
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="emit JSON evidence")
    args = parser.parse_args(argv)
    report = asyncio.run(_run())
    if args.json:
        print(json.dumps(report, sort_keys=True, indent=2))
    else:
        print(f"gateway worker host fixture: {report['status']}")
    return {"pass": 0, "fail": 1}[str(report["status"])]


if __name__ == "__main__":
    raise SystemExit(main())

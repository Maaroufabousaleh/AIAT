"""Certify gateway-worker failure classification through host execution.

This companion fixture drives the real ``WorkerHostExecutor``,
``WorkerRunController``, and ``GatewayWorkerAdapter`` twice over the bounded
in-memory host fixture: once for a retryable gateway status and once for a
permanent gateway rejection.  It proves terminal settlement, secret-safe
classification, and binding release without contacting a provider or claiming
durable host, sandbox, or recovery evidence.
"""

from __future__ import annotations

import argparse
import asyncio
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any
from uuid import UUID

MAS_ROOT = Path(__file__).resolve().parents[1]
CORE_ROOT = MAS_ROOT / "packages" / "mas-core"
if str(CORE_ROOT) not in sys.path:
    sys.path.insert(0, str(CORE_ROOT))

from mas_core.llm_gateway.client import LLMGatewayError  # noqa: E402
from mas_core.worker_contract.models import ModelProfileReference, WorkerRunRequest  # noqa: E402
from mas_core.worker_registry.host_executor import (  # noqa: E402
    HostExecutionRequest,
    WorkerHostExecutor,
)
from mas_core.worker_registry.runtime_adapters import GatewayWorkerAdapter  # noqa: E402

CHECK_SCHEMA = "aiat.gateway-worker-host-failure-fixture.v1"
RUN_ID = UUID("00000000-0000-4000-a000-000000000c41")
WORKER_ID = UUID("00000000-0000-4000-a000-000000000c42")
HOST_ID = "gateway-worker-host-fixture"
TRACE_ID = "gateway-worker-host-failure-trace"
SPAN_ID = "gateway-worker-host-failure-span"
IDEMPOTENCY_KEY = "gateway-worker-host-failure-idempotency"
OWNER = "gateway-worker-host-fixture-owner"
PRIVATE_MARKER = "gateway provider private detail marker"


def _load_success_fixture() -> Any:
    """Load the bounded host doubles from the companion success certificate."""

    path = Path(__file__).with_name("check_gateway_worker_host_fixture.py")
    spec = importlib.util.spec_from_file_location("gateway_worker_host_fixture", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("unable to load gateway worker host fixture")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _FailingGateway:
    """Gateway double that exposes status metadata but never provider text."""

    def __init__(self, status_code: int) -> None:
        self.status_code = status_code
        self.calls: list[dict[str, Any]] = []

    async def chat_completion(self, **kwargs: Any) -> Any:
        self.calls.append(dict(kwargs))
        raise LLMGatewayError(self.status_code, PRIVATE_MARKER)


def _request() -> WorkerRunRequest:
    return WorkerRunRequest(
        run_id=RUN_ID,
        idempotency_key=IDEMPOTENCY_KEY,
        worker_id=str(WORKER_ID),
        task_type="gateway-worker-host-failure-fixture",
        task_input={
            "prompt": "exercise bounded gateway failure classification",
            "max_tokens": 16,
            "temperature": 0.2,
            "private_marker": PRIVATE_MARKER,
        },
        trace_id=TRACE_ID,
        span_id=SPAN_ID,
        resolved_model_profile=ModelProfileReference(
            profile_id="gateway-host-failure-profile-v1",
            version="fixture-v1",
            exact_model_id="fixture/model-v1",
        ),
        timeout_seconds=30,
    )


async def _run_case(status_code: int) -> dict[str, Any]:
    fixture = _load_success_fixture()
    storage = fixture._MemoryStorage()
    bindings = fixture._MemoryBindingService()
    gateway = _FailingGateway(status_code)
    adapter = GatewayWorkerAdapter(
        worker_id=str(WORKER_ID),
        provider_id="fixture-provider",
        gateway_client=gateway,
        runtime_version="gateway-host-failure-fixture-v1",
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
            _request(),
            adapter,
            worker_registry_id=WORKER_ID,
        )
    finally:
        await adapter.close()

    error_event = next(
        (event for event in execution.outcome.events if event.error is not None),
        None,
    )
    error = error_event.error if error_event is not None else None
    evidence_payload = {
        "events": storage.events,
        "run": storage.run,
        "usage": storage.usage,
    }
    payload_free = PRIVATE_MARKER not in json.dumps(evidence_payload, default=str)
    expected_retryable = status_code in {408, 409, 412, 425, 429, 500, 502, 503, 504}
    expected_code = (
        "MODEL_GATEWAY_TRANSIENT_FAILURE"
        if expected_retryable
        else "MODEL_GATEWAY_REQUEST_REJECTED"
    )
    return {
        "status_code": status_code,
        "status": "pass"
        if all(
            (
                execution.outcome.state == "FAILED",
                error is not None,
                error.code == expected_code,
                error.retryable is expected_retryable,
                error.terminal is (not expected_retryable),
                error.category == "provider",
                error.details.get("status_code") == status_code,
                error.cause_type == "LLMGatewayError",
                len(gateway.calls) == 1,
                execution.binding_before.get("state") == "COMMITTED",
                execution.binding_after.get("state") == "RELEASED",
                execution.binding_after.get("reservation_state") == "RELEASED",
                len(storage.events) >= 2,
                len(storage.usage) == 1,
                payload_free,
            )
        )
        else "fail",
        "run_state": execution.outcome.state,
        "error_code": error.code if error is not None else None,
        "retryable": error.retryable if error is not None else None,
        "terminal": error.terminal if error is not None else None,
        "category": error.category if error is not None else None,
        "reported_status_code": (
            error.details.get("status_code") if error is not None else None
        ),
        "cause_type": error.cause_type if error is not None else None,
        "gateway_call_count": len(gateway.calls),
        "binding_before": execution.binding_before.get("state"),
        "binding_after": execution.binding_after.get("state"),
        "reservation_after": execution.binding_after.get("reservation_state"),
        "event_count": len(storage.events),
        "usage_count": len(storage.usage),
        "payload_free_report": payload_free,
    }


async def _run() -> dict[str, Any]:
    cases = [await _run_case(429), await _run_case(401)]
    return {
        "schema_version": CHECK_SCHEMA,
        "mode": "in-memory-gateway-worker-host-failure-fixture",
        "status": "pass" if all(case["status"] == "pass" for case in cases) else "fail",
        "cases": cases,
        "mutation_performed": True,
        "network_access_performed": False,
        "external_provider_call_performed": False,
        "sandbox_execution_performed": False,
        "licence_metadata_is_gate": False,
        "scope": "real WorkerHostExecutor/WorkerRunController/GatewayWorkerAdapter over bounded in-memory failure fixture; no durable host, provider, sandbox, or live recovery claim",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="emit JSON evidence")
    args = parser.parse_args(argv)
    report = asyncio.run(_run())
    if args.json:
        print(json.dumps(report, sort_keys=True, indent=2))
    else:
        print(f"gateway worker host failure fixture: {report['status']}")
    return {"pass": 0, "fail": 1}[str(report["status"])]


if __name__ == "__main__":
    raise SystemExit(main())

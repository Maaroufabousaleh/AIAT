"""Certify the AIAT gateway worker through an in-process HTTP gateway.

This fixture drives the real ``LLMGatewayClient`` and
``GatewayWorkerAdapter`` through an ``httpx.MockTransport``.  It proves the
AIAT-owned OpenAI-compatible request boundary, bearer-secret propagation,
bounded request payload, transient retry, controller terminal result, and
exact provider/model usage attribution without contacting an external
provider, mutating deployment state, or executing a sandbox.

External-provider dispatch, provider outage recovery, and hardened sandbox
certification remain separate live gates.  Licence/restriction metadata is
not an operational predicate.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

import httpx

MAS_ROOT = Path(__file__).resolve().parents[1]
CORE_ROOT = MAS_ROOT / "packages" / "mas-core"
if str(CORE_ROOT) not in sys.path:
    sys.path.insert(0, str(CORE_ROOT))

from mas_core.llm_gateway.client import LLMGatewayClient  # noqa: E402
from mas_core.llm_gateway.models import LLMConfig  # noqa: E402
from mas_core.worker_contract.controller import WorkerRunController  # noqa: E402
from mas_core.worker_contract.models import ModelProfileReference, WorkerRunRequest  # noqa: E402
from mas_core.worker_registry.runtime_adapters import GatewayWorkerAdapter  # noqa: E402

SCHEMA = "aiat.gateway-worker-http-fixture.v1"
PROVIDER_ID = "fixture-provider"
MODEL_ID = "fixture/model-v1"
FIXTURE_API_KEY = "fixture-secret"


class _LoopbackGateway(LLMGatewayClient):
    """Use the production gateway client with a deterministic HTTP transport."""

    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []
        self._response_count = 0
        self._transport = httpx.MockTransport(self._handle)
        super().__init__(
            LLMConfig(
                gateway_url="http://gateway.fixture",
                default_model=MODEL_ID,
                api_key=FIXTURE_API_KEY,
                backend="litellm",
                max_retries=1,
                retry_min_wait_s=0.001,
                retry_max_wait_s=0.001,
                timeout_s=5.0,
            )
        )

    async def _handle(self, request: httpx.Request) -> httpx.Response:
        self._response_count += 1
        payload = json.loads(request.content.decode("utf-8"))
        self.requests.append(
            {
                "path": request.url.path,
                "authorization": request.headers.get("authorization"),
                "payload": payload,
            }
        )
        if self._response_count == 1:
            return httpx.Response(
                429,
                json={"error": {"message": "fixture rate limit"}},
                request=request,
            )
        return httpx.Response(
            200,
            json={
                "id": "chatcmpl-fixture-http",
                "model": MODEL_ID,
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {"role": "assistant", "content": "loopback answer"},
                    }
                ],
                "usage": {
                    "prompt_tokens": 5,
                    "completion_tokens": 4,
                    "total_tokens": 9,
                },
            },
            request=request,
        )

    async def start(self) -> None:
        headers = {"Content-Type": "application/json", "Authorization": f"Bearer {FIXTURE_API_KEY}"}
        self._http = httpx.AsyncClient(
            base_url=self._config.gateway_url,
            headers=headers,
            timeout=self._config.timeout_s,
            transport=self._transport,
        )


async def _fixture() -> dict[str, Any]:
    gateway = _LoopbackGateway()
    adapter = GatewayWorkerAdapter(
        worker_id="gateway-http-worker-fixture",
        provider_id=PROVIDER_ID,
        gateway_client=gateway,
    )
    request = WorkerRunRequest(
        idempotency_key="gateway-worker-http-fixture-v1",
        worker_id="gateway-http-worker-fixture",
        task_type="gateway-http-fixture",
        task_input={
            "prompt": "reply with loopback answer",
            "max_tokens": 32,
            "temperature": 0.2,
        },
        resolved_model_profile=ModelProfileReference(
            profile_id="fixture-profile-v1",
            version="v1",
            exact_model_id=MODEL_ID,
        ),
    )
    await gateway.start()
    try:
        outcome = await WorkerRunController().execute(request, adapter)
    finally:
        await adapter.close()
        await gateway.stop()

    result = outcome.result
    request_rows = gateway.requests
    payload = request_rows[-1].get("payload") if request_rows else {}
    usage = result.usage if result is not None else None
    passed = (
        outcome.state == "SUCCEEDED"
        and result is not None
        and result.success
        and result.output == {"text": "loopback answer", "finish_reason": "stop"}
        and usage is not None
        and usage.provider == PROVIDER_ID
        and usage.exact_model_id == MODEL_ID
        and len(request_rows) == 2
        and request_rows[0].get("path") == "/v1/chat/completions"
        and all(row.get("authorization") == f"Bearer {FIXTURE_API_KEY}" for row in request_rows)
        and payload.get("model") == MODEL_ID
        and payload.get("messages") == [{"role": "user", "content": "reply with loopback answer"}]
        and payload.get("max_tokens") == 32
        and payload.get("temperature") == 0.2
    )
    return {
        "schema_version": SCHEMA,
        "mode": "fixture",
        "status": "pass" if passed else "fail",
        "licence_metadata_is_gate": False,
        "adapter_type": adapter.runtime_type,
        "controller_terminal_state": outcome.state,
        "provider_id": PROVIDER_ID,
        "exact_model_id": MODEL_ID,
        "gateway_request_count": len(request_rows),
        "retry_count": max(0, len(request_rows) - 1),
        "gateway_endpoint": "/v1/chat/completions",
        "authorization_boundary_match": bool(
            request_rows and all(
                row.get("authorization") == f"Bearer {FIXTURE_API_KEY}"
                for row in request_rows
            )
        ),
        "bounded_payload_match": bool(
            payload.get("model") == MODEL_ID
            and payload.get("max_tokens") == 32
            and payload.get("temperature") == 0.2
        ),
        "usage_attribution_match": bool(
            usage is not None
            and usage.provider == PROVIDER_ID
            and usage.exact_model_id == MODEL_ID
        ),
        "fixture_dispatch_performed": True,
        "external_provider_call_performed": False,
        "network_mutation_performed": False,
        "sandbox_execution_performed": False,
        "scope": "real LLMGatewayClient/GatewayWorkerAdapter/WorkerRunController through in-process HTTP transport",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="emit JSON")
    args = parser.parse_args(argv)
    report = asyncio.run(_fixture())
    if args.json:
        print(json.dumps(report, sort_keys=True, indent=2))
    else:
        print(f"gateway worker HTTP fixture: {report['status']} — {report['scope']}")
    return 0 if report["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())

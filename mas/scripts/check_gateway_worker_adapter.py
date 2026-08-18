"""Run the deterministic AIAT model-gateway worker adapter fixture.

The fixture drives the real ``GatewayWorkerAdapter`` and
``WorkerRunController`` with an in-process gateway double.  It proves bounded
request normalization, exact-model usage attribution, and terminal result
normalization without making a provider, network, deployment, or sandbox
call.  External-provider dispatch and hardened sandbox certification remain
separate live gates.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

MAS_ROOT = Path(__file__).resolve().parents[1]
CORE_ROOT = MAS_ROOT / "packages" / "mas-core"
if str(CORE_ROOT) not in sys.path:
    sys.path.insert(0, str(CORE_ROOT))

from mas_core.llm_gateway.models import ChatMessage, ChatResponse, UsageStats  # noqa: E402
from mas_core.worker_contract.controller import WorkerRunController  # noqa: E402
from mas_core.worker_contract.models import ModelProfileReference, WorkerRunRequest  # noqa: E402
from mas_core.worker_registry.runtime_adapters import GatewayWorkerAdapter  # noqa: E402

SCHEMA = "aiat.gateway-worker-adapter.v1"
PROVIDER_ID = "fixture-provider"
MODEL_ID = "fixture/model-v1"


class _FixtureGateway:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def chat_completion(self, **kwargs: Any) -> ChatResponse:
        self.calls.append(kwargs)
        return ChatResponse(
            model=str(kwargs["model"]),
            message=ChatMessage(role="assistant", content="fixture answer"),
            usage=UsageStats(prompt_tokens=4, completion_tokens=3, total_tokens=7),
        )


async def _fixture() -> dict[str, Any]:
    gateway = _FixtureGateway()
    adapter = GatewayWorkerAdapter(
        worker_id="gateway-worker-fixture",
        provider_id=PROVIDER_ID,
        gateway_client=gateway,
    )
    request = WorkerRunRequest(
        idempotency_key="gateway-worker-adapter-fixture-v1",
        worker_id="gateway-worker-fixture",
        task_type="gateway-adapter-fixture",
        task_input={"prompt": "reply with fixture answer", "max_tokens": 32},
        resolved_model_profile=ModelProfileReference(
            profile_id="fixture-profile-v1",
            version="v1",
            exact_model_id=MODEL_ID,
        ),
    )
    try:
        outcome = await WorkerRunController().execute(request, adapter)
    finally:
        await adapter.close()
    result = outcome.result
    passed = (
        outcome.state == "SUCCEEDED"
        and result is not None
        and result.success
        and result.usage.provider == PROVIDER_ID
        and result.usage.exact_model_id == MODEL_ID
        and len(gateway.calls) == 1
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
        "gateway_request_count": len(gateway.calls),
        "fixture_dispatch_performed": True,
        "external_provider_call_performed": False,
        "network_mutation_performed": False,
        "sandbox_execution_performed": False,
        "usage_attribution_match": bool(
            result is not None
            and result.usage.provider == PROVIDER_ID
            and result.usage.exact_model_id == MODEL_ID
        ),
        "scope": "real GatewayWorkerAdapter/WorkerRunController fixture; no external provider or deployment state changed",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="emit JSON")
    args = parser.parse_args(argv)
    report = asyncio.run(_fixture())
    if args.json:
        print(json.dumps(report, sort_keys=True, indent=2))
    else:
        print(f"gateway worker adapter: {report['status']} — {report['scope']}")
    return 0 if report["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())

"""Run a bounded local gateway provider-outage and recovery fixture.

The fixture exercises AIAT's existing model/provider fallback and cooldown
boundary with two explicitly registered providers.  The first request forces
one transport outage on the primary provider, succeeds through the secondary,
then lets the bounded cooldown expire and verifies that a recovered primary is
selected again.  It never contacts an external provider, dispatches a worker,
or persists durable state; external-provider recovery remains a separate gate.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any
from unittest.mock import patch

import httpx

MAS_ROOT = Path(__file__).resolve().parents[1]
CORE_ROOT = MAS_ROOT / "packages" / "mas-core"
if str(CORE_ROOT) not in sys.path:
    sys.path.insert(0, str(CORE_ROOT))

from mas_core.llm_gateway.client import LLMGatewayClient  # noqa: E402
from mas_core.llm_gateway.models import LLMConfig  # noqa: E402
from mas_core.llm_gateway.providers import (  # noqa: E402
    ApiStyle,
    ModelEntry,
    ModelRegistry,
    ProviderConfig,
)
from mas_core.llm_gateway.rate_limits import RateLimitTracker  # noqa: E402

SCHEMA = "aiat.gateway-provider-recovery.v1"
PRIMARY_PROVIDER = "fixture-provider-primary"
SECONDARY_PROVIDER = "fixture-provider-secondary"
PRIMARY_MODEL = "fixture-primary/model-v1"
SECONDARY_MODEL = "fixture-secondary/model-v1"


def _registry() -> ModelRegistry:
    registry = ModelRegistry()
    registry.register_provider(
        ProviderConfig(
            provider_id=PRIMARY_PROVIDER,
            base_url="https://primary.fixture.invalid",
            default_api_key="fixture-primary-key",
        )
    )
    registry.register_provider(
        ProviderConfig(
            provider_id=SECONDARY_PROVIDER,
            base_url="https://secondary.fixture.invalid",
            default_api_key="fixture-secondary-key",
        )
    )
    registry.register(
        ModelEntry(
            model_id=PRIMARY_MODEL,
            provider=PRIMARY_PROVIDER,
            api_style=ApiStyle.CHAT_COMPLETIONS,
            endpoint="https://primary.fixture.invalid/v1/chat/completions",
        )
    )
    registry.register(
        ModelEntry(
            model_id=SECONDARY_MODEL,
            provider=SECONDARY_PROVIDER,
            api_style=ApiStyle.CHAT_COMPLETIONS,
            endpoint="https://secondary.fixture.invalid/v1/chat/completions",
        )
    )
    return registry


async def _run() -> dict[str, Any]:
    client = LLMGatewayClient(
        LLMConfig(
            gateway_url="https://gateway.fixture.invalid",
            default_model=PRIMARY_MODEL,
            api_key="fixture-gateway-key",
            backend="legacy",
            timeout_s=1.0,
            max_retries=0,
            retry_min_wait_s=0.0,
            retry_max_wait_s=0.0,
        ),
        registry=_registry(),
        rate_limit_tracker=RateLimitTracker(
            cooldown_base_s=0.1,
            cooldown_max_s=0.1,
            provider_cooldown_threshold=1,
        ),
    )
    attempts: list[str] = []
    primary_recovered = False

    async def fake_post(_client: httpx.AsyncClient, _url: str, **kwargs: Any) -> httpx.Response:
        nonlocal primary_recovered
        payload = kwargs.get("json") or {}
        model = str(payload.get("model") or "")
        attempts.append(model)
        if model == PRIMARY_MODEL and not primary_recovered:
            raise httpx.ConnectError("fixture provider outage")
        return httpx.Response(
            200,
            json={
                "id": "fixture-response",
                "model": model,
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {"role": "assistant", "content": "fixture recovered"},
                    }
                ],
                "usage": {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5},
            },
        )

    try:
        with patch.object(httpx.AsyncClient, "post", new=fake_post):
            async with client:
                with patch.object(
                    client.model_selector,
                    "fallback_chain",
                    return_value=[PRIMARY_MODEL, SECONDARY_MODEL],
                ):
                    first = await client.chat_completion_with_fallback(
                        [{"role": "user", "content": "fixture outage"}],
                        model=PRIMARY_MODEL,
                        chain_length=2,
                    )
                    outage_state = client.rate_limits.cooldown_status(
                        PRIMARY_MODEL,
                        provider=PRIMARY_PROVIDER,
                    )
                    await asyncio.sleep(0.2)
                    primary_recovered = True
                    second = await client.chat_completion_with_fallback(
                        [{"role": "user", "content": "fixture recovery"}],
                        model=PRIMARY_MODEL,
                        chain_length=2,
                    )
                    recovery_state = client.rate_limits.cooldown_status(
                        PRIMARY_MODEL,
                        provider=PRIMARY_PROVIDER,
                    )
    except Exception as exc:  # pragma: no cover - report converts unexpected fixture failures
        return {
            "schema_version": SCHEMA,
            "mode": "fixture",
            "status": "fail",
            "licence_metadata_is_gate": False,
            "reason": type(exc).__name__,
            "network_access_performed": False,
            "external_provider_call_performed": False,
            "durable_worker_state_changed": False,
            "scope": "local gateway fallback/cooldown fixture only",
        }

    expected_attempts = [PRIMARY_MODEL, SECONDARY_MODEL, PRIMARY_MODEL]
    passed = (
        first.model == SECONDARY_MODEL
        and second.model == PRIMARY_MODEL
        and attempts == expected_attempts
        and outage_state["in_cooldown"] is True
        and recovery_state["in_cooldown"] is False
    )
    return {
        "schema_version": SCHEMA,
        "mode": "fixture",
        "status": "pass" if passed else "fail",
        "licence_metadata_is_gate": False,
        "attempt_models": attempts,
        "fallback_used": first.model == SECONDARY_MODEL,
        "primary_recovered": second.model == PRIMARY_MODEL,
        "primary_cooldown_after_outage": bool(outage_state["in_cooldown"]),
        "provider_cooldown_after_outage": any(
            scope["key"] == f"provider:{PRIMARY_PROVIDER}" and scope["in_cooldown"]
            for scope in outage_state["scopes"]
        ),
        "cooldown_cleared_after_primary_recovery": not bool(recovery_state["in_cooldown"]),
        "request_count": len(attempts),
        "network_access_performed": False,
        "external_provider_call_performed": False,
        "durable_worker_state_changed": False,
        "scope": "local LLMGatewayClient fallback/cooldown fixture; no external provider or durable worker state",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="emit JSON")
    args = parser.parse_args(argv)
    report = asyncio.run(_run())
    if args.json:
        print(json.dumps(report, sort_keys=True, indent=2))
    else:
        print(f"gateway provider recovery: {report['status']} — {report['scope']}")
    return 0 if report["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())

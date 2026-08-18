"""Run one explicitly selected model through the AIAT worker plane.

The checker is the live boundary for the remaining provider-backed worker
slice.  It uses the production ``WorkerHostExecutor``, ``WorkerRunController``,
and ``GatewayWorkerAdapter`` with a bounded in-memory host binding, then sends
one small completion through the configured AIAT gateway.  It is deliberately
opt-in: without ``--allow-external-provider`` (or
``AIAT_ALLOW_EXTERNAL_PROVIDER_DISPATCH=1``) it performs no network call and
returns ``blocked``.  The selected model must be explicit and present in the
gateway's read-only ``/v1/models`` listing before dispatch.

This is not a durable Postgres, independent-host, sandbox, provider-outage,
or mail-edge callback certificate.  Generated text is never printed or put in
the report; only bounded status, usage, model, and error-category metadata is
retained.  Licence metadata is not an operational gate.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx

MAS_ROOT = Path(__file__).resolve().parents[1]
CORE_ROOT = MAS_ROOT / "packages" / "mas-core"
SCRIPTS_ROOT = MAS_ROOT / "scripts"
for _path in (CORE_ROOT, SCRIPTS_ROOT):
    if _path.exists() and str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

import check_gateway_worker_host_fixture as _host_fixture  # noqa: E402

from mas_core.llm_gateway import LLMConfig, LLMGatewayClient  # noqa: E402
from mas_core.worker_contract.models import (  # noqa: E402
    ModelProfileReference,
    WorkerRunRequest,
)
from mas_core.worker_registry.host_executor import (  # noqa: E402
    HOST_EXECUTION_SCHEMA,
    HostExecutionRequest,
    WorkerHostExecutor,
)
from mas_core.worker_registry.runtime_adapters import GatewayWorkerAdapter  # noqa: E402

CHECK_SCHEMA = "aiat.gateway-worker-provider-live.v1"
RUN_ID = _host_fixture.RUN_ID
WORKER_ID = _host_fixture.WORKER_ID
HOST_ID = _host_fixture.HOST_ID
TRACE_ID = "gateway-worker-provider-live-trace"
SPAN_ID = "gateway-worker-provider-live-span"
IDEMPOTENCY_KEY = "gateway-worker-provider-live-idempotency"
OWNER = _host_fixture.OWNER
PROMPT = "Reply with exactly the single word: ready"
MAX_TOKENS = 16
TEMPERATURE = 0.0


def _truthy(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _configured_url() -> str:
    explicit = os.getenv("AIAT_LIVE_LLM_GATEWAY_URL", "").strip()
    if explicit:
        return explicit.rstrip("/")
    configured = os.getenv("LLM_GATEWAY_URL", "").strip()
    if not configured:
        return ""
    parsed = urlparse(configured)
    # A service name is valid from a Compose container but not from the host.
    # The operator can override this explicitly; do not guess another route.
    if parsed.hostname in {"litellm", "omniroute"}:
        return ""
    return configured.rstrip("/")


def _configured_key() -> str:
    return (
        os.getenv("AIAT_LIVE_LLM_API_KEY", "").strip()
        or os.getenv("LLM_API_KEY", "").strip()
        or os.getenv("LITELLM_MASTER_KEY", "").strip()
        or os.getenv("OMNIROUTE_API_KEY", "").strip()
        or os.getenv("MAS_API_KEY", "").strip()
    )


def _blocked(reason: str, **details: Any) -> dict[str, Any]:
    report: dict[str, Any] = {
        "schema_version": CHECK_SCHEMA,
        "execution_schema": HOST_EXECUTION_SCHEMA,
        "mode": "selected-worker-plane-provider",
        "status": "blocked",
        "reason": reason,
        "mutation_performed": False,
        "network_access_performed": False,
        "external_network_access_performed": False,
        "provider_dispatch_attempted": False,
        "external_provider_call_performed": False,
        "worker_dispatch_performed": False,
        "sandbox_execution_performed": False,
        "licence_metadata_is_gate": False,
    }
    report.update(details)
    return report


def _validate_url(url: str) -> bool:
    parsed = urlparse(url.strip())
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


async def _listed_models(
    *,
    gateway_url: str,
    api_key: str,
    timeout_s: float,
) -> tuple[set[str], int]:
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    async with httpx.AsyncClient(
        base_url=gateway_url.rstrip("/"),
        headers=headers,
        timeout=timeout_s,
    ) as client:
        response = await client.get("/v1/models")
        response.raise_for_status()
        payload = response.json()
    rows = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        raise ValueError("gateway model listing is malformed")
    model_ids = {
        str(row.get("id")).strip()
        for row in rows
        if isinstance(row, dict)
        and isinstance(row.get("id"), str)
        and str(row.get("id")).strip()
    }
    return model_ids, len(model_ids)


class _CountingGateway:
    """Keep only call count and scalar request metadata around the client."""

    def __init__(self, delegate: Any) -> None:
        self.delegate = delegate
        self.calls: list[dict[str, Any]] = []

    async def chat_completion(self, **kwargs: Any) -> Any:
        self.calls.append(
            {
                "model": str(kwargs.get("model") or ""),
                "max_tokens": int(kwargs.get("max_tokens") or 0),
                "temperature": float(kwargs.get("temperature") or 0),
                "message_count": len(kwargs.get("messages") or []),
            }
        )
        return await self.delegate.chat_completion(**kwargs)


async def _run(
    *,
    gateway_url: str | None,
    api_key: str | None,
    model_id: str | None,
    provider_id: str,
    allow_external_provider: bool,
    timeout_s: float = 20.0,
    gateway_client: Any | None = None,
    listed_model_ids: set[str] | None = None,
) -> dict[str, Any]:
    """Run the bounded live edge; ``gateway_client`` is test-only injection."""

    if not allow_external_provider:
        return _blocked(
            "external_provider_dispatch_requires_explicit_opt_in",
            opt_in_environment="AIAT_ALLOW_EXTERNAL_PROVIDER_DISPATCH",
        )
    selected_model = str(model_id or "").strip()
    if not selected_model or selected_model.lower() == "auto":
        return _blocked("selected_exact_model_id_is_required")
    normalized_provider = str(provider_id or "").strip()
    if not normalized_provider:
        return _blocked("provider_id_is_required")
    if timeout_s < 1 or timeout_s > 120:
        return _blocked("timeout_must_be_between_one_and_120_seconds")

    injected_gateway = gateway_client is not None
    selected_url = str(gateway_url or "").strip().rstrip("/")
    selected_key = str(api_key or "").strip()
    model_count: int | None = None
    if injected_gateway and listed_model_ids is not None:
        model_count = len(listed_model_ids)
        if selected_model not in listed_model_ids:
            return _blocked(
                "selected_model_is_not_listed_by_injected_gateway",
                selected_model_id=selected_model,
                gateway_model_count=model_count,
            )
    if not injected_gateway:
        if not _validate_url(selected_url):
            return _blocked("live_gateway_url_is_missing_or_invalid")
        if not selected_key:
            return _blocked("live_gateway_api_key_is_missing")
        try:
            listed_model_ids, model_count = await _listed_models(
                gateway_url=selected_url,
                api_key=selected_key,
                timeout_s=timeout_s,
            )
        except httpx.HTTPStatusError as exc:
            return _blocked(
                "live_gateway_model_listing_rejected",
                gateway_http_status=exc.response.status_code,
                network_access_performed=True,
                external_network_access_performed=True,
            )
        except (httpx.HTTPError, ValueError) as exc:
            return _blocked(
                "live_gateway_model_listing_unavailable",
                error_type=type(exc).__name__,
                network_access_performed=True,
                external_network_access_performed=True,
            )
        if selected_model not in listed_model_ids:
            return _blocked(
                "selected_model_is_not_listed_by_live_gateway",
                selected_model_id=selected_model,
                gateway_model_count=model_count,
                network_access_performed=True,
                external_network_access_performed=True,
            )
        config = LLMConfig.model_construct(
            backend="litellm",
            gateway_url=selected_url,
            api_key=selected_key,
            default_model=selected_model,
            timeout_s=timeout_s,
            max_retries=1,
            retry_min_wait_s=0.25,
            retry_max_wait_s=1.0,
        )
        client = LLMGatewayClient(config)
        await client.start()
        delegate = client
    else:
        client = None
        delegate = gateway_client

    counting_gateway = _CountingGateway(delegate)
    storage = _host_fixture._MemoryStorage()
    bindings = _host_fixture._MemoryBindingService()
    request = WorkerRunRequest(
        run_id=RUN_ID,
        idempotency_key=IDEMPOTENCY_KEY,
        worker_id=str(WORKER_ID),
        task_type="gateway_worker_provider_live",
        task_input={
            "prompt": PROMPT,
            "max_tokens": MAX_TOKENS,
            "temperature": TEMPERATURE,
        },
        trace_id=TRACE_ID,
        span_id=SPAN_ID,
        resolved_model_profile=ModelProfileReference(
            profile_id="selected-live-provider-profile",
            version="operator-selected",
            exact_model_id=selected_model,
        ),
        timeout_seconds=30,
    )
    adapter = GatewayWorkerAdapter(
        worker_id=str(WORKER_ID),
        provider_id=normalized_provider,
        gateway_client=counting_gateway,
        runtime_version="gateway-worker-provider-live-v1",
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
        if client is not None:
            await client.stop()

    outcome = execution.outcome
    result = outcome.result
    usage = result.usage if result is not None and result.success else None
    error = result.error if result is not None and not result.success else None
    passed = all(
        (
            outcome.state == "SUCCEEDED",
            result is not None and result.success,
            len(counting_gateway.calls) == 1,
            counting_gateway.calls[0].get("model") == selected_model,
            usage is not None and usage.provider == normalized_provider,
            usage is not None and usage.exact_model_id == selected_model,
            execution.binding_before.get("state") == "COMMITTED",
            execution.binding_after.get("state") == "RELEASED",
        )
    )
    return {
        "schema_version": CHECK_SCHEMA,
        "execution_schema": HOST_EXECUTION_SCHEMA,
        "mode": "selected-worker-plane-provider",
        "status": "pass" if passed else "fail",
        "observed_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "selected_model_id": selected_model,
        "provider_id": normalized_provider,
        "gateway_model_count": model_count,
        "gateway_call_count": len(counting_gateway.calls),
        "controller_terminal_state": outcome.state,
        "worker_dispatch_performed": True,
        "provider_dispatch_attempted": bool(counting_gateway.calls),
        "external_provider_call_performed": bool(
            not injected_gateway and counting_gateway.calls and outcome.state == "SUCCEEDED"
        ),
        "gateway_completion_succeeded": outcome.state == "SUCCEEDED",
        "usage": {
            "prompt_tokens": int(getattr(usage, "prompt_tokens", 0) or 0),
            "completion_tokens": int(getattr(usage, "completion_tokens", 0) or 0),
            "total_tokens": int(getattr(usage, "total_tokens", 0) or 0),
            "provider_id": getattr(usage, "provider", None),
            "exact_model_id": getattr(usage, "exact_model_id", None),
        },
        "error": {
            "code": getattr(error, "code", None),
            "category": getattr(error, "category", None),
            "retryable": getattr(error, "retryable", None),
            "terminal": getattr(error, "terminal", None),
        },
        "host_admission": {
            "host_id": HOST_ID,
            "host_plane": execution.binding_before.get("host_plane"),
            "binding_before": execution.binding_before.get("state"),
            "binding_after": execution.binding_after.get("state"),
            "reservation_before": execution.binding_before.get("reservation_state"),
            "reservation_after": execution.binding_after.get("reservation_state"),
        },
        "network_access_performed": not injected_gateway,
        "gateway_network_access_performed": not injected_gateway,
        "external_network_access_performed": not injected_gateway,
        "mutation_performed": True,
        "external_mutation_performed": False,
        "sandbox_execution_performed": False,
        "durable_postgres_evidence": False,
        "mail_edge_callback_evidence": False,
        "licence_metadata_is_gate": False,
        "scope": "one bounded AIAT GatewayWorkerAdapter/WorkerHostExecutor dispatch with operator-selected model and no generated-content persistence",
        "certification_boundary": {
            "selected_model_listing": "checked" if not injected_gateway else "injected_test_only",
            "worker_host_admission_and_release": "checked",
            "gateway_provider_dispatch": "checked" if passed and not injected_gateway else "not_checked",
            "durable_postgres_worker_evidence": "not_checked",
            "independent_host": "not_checked",
            "sandbox_runtime_gvisor_or_firecracker": "not_checked",
            "provider_backed_recovery": "not_checked",
            "provider_mail_callback_and_bounce": "not_checked",
        },
        "licence_metadata": "metadata-only; no activation or execution gate",
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="emit JSON evidence")
    parser.add_argument(
        "--allow-external-provider",
        action="store_true",
        default=_truthy(os.getenv("AIAT_ALLOW_EXTERNAL_PROVIDER_DISPATCH")),
        help="explicitly permit one bounded completion request",
    )
    parser.add_argument("--url", default=_configured_url(), help="live AIAT gateway URL")
    parser.add_argument("--api-key", default=_configured_key(), help="live gateway bearer key")
    parser.add_argument(
        "--model",
        default=os.getenv("AIAT_LIVE_WORKER_MODEL", ""),
        help="exact selected model/alias; auto is rejected",
    )
    parser.add_argument(
        "--provider-id",
        default=os.getenv("AIAT_LIVE_WORKER_PROVIDER_ID", "litellm"),
        help="AIAT provider identity recorded in usage metadata",
    )
    parser.add_argument("--timeout", type=float, default=20.0)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    report = asyncio.run(
        _run(
            gateway_url=args.url,
            api_key=args.api_key,
            model_id=args.model,
            provider_id=args.provider_id,
            allow_external_provider=args.allow_external_provider,
            timeout_s=args.timeout,
        )
    )
    if args.json:
        print(json.dumps(report, sort_keys=True, indent=2, default=str))
    else:
        print(f"gateway worker provider live: {report['status']} — {report.get('reason') or report['scope']}")
    return {"pass": 0, "fail": 1, "blocked": 2}[str(report["status"])]


if __name__ == "__main__":
    raise SystemExit(main())

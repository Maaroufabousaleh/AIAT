"""Internet-facing provider ingress and bounded outbox delivery trigger.

Provider credentials stay in the orchestrator credential boundary. The gateway
forwards only raw webhook bodies and safe provider delivery headers to the
authenticated orchestrator endpoint, then asks the orchestrator to drain a
bounded outbox batch. This keeps external traffic isolated without creating a
second canonical state writer.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
from contextlib import asynccontextmanager, suppress
from uuid import UUID  # noqa: TC003

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response

from .config import Settings

logger = logging.getLogger(__name__)
settings = Settings()
_webhook_requests = 0
_webhook_failures = 0
_drain_failures = 0


def _headers() -> dict[str, str]:
    if not settings.pm_gateway_api_key:
        raise RuntimeError("PM_GATEWAY_API_KEY is required for orchestrator calls")
    return {"X-API-Key": settings.pm_gateway_api_key}


def _safe_provider_headers(request: Request) -> dict[str, str]:
    allowed = {
        "x-github-delivery",
        "x-github-event",
        "x-hub-signature-256",
        "x-youtrack-token",
        "x-youtrack-delivery",
        "x-delivery-id",
        "x-webhook-signature",
        "x-provider-signature",
        "x-signature",
        "x-event-id",
        "content-type",
    }
    return {
        key: value
        for key, value in request.headers.items()
        if key.lower() in allowed or key.lower().startswith("x-youtrack-")
    }


async def _forward_to_orchestrator(connection_id: UUID, body: bytes, headers: dict[str, str]) -> httpx.Response:
    """Forward an already-read provider request over the private service link."""
    async with httpx.AsyncClient(timeout=30.0) as client:
        return await client.post(
            f"{settings.orchestrator_url.rstrip('/')}/integrations/webhooks/{connection_id}",
            content=body,
            headers={**_headers(), **headers},
        )


async def _drain_loop(stop: asyncio.Event) -> None:
    global _drain_failures
    while not stop.is_set():
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    f"{settings.orchestrator_url.rstrip('/')}/integrations/outbox/drain",
                    params={"limit": settings.outbox_batch_size},
                    headers=_headers(),
                )
                if response.status_code >= 400:
                    _drain_failures += 1
                    logger.warning("pm_gateway_drain_failed status=%s body=%s", response.status_code, response.text[:300])
        except asyncio.CancelledError:
            break
        except Exception:
            logger.warning("pm_gateway_drain_error", exc_info=True)
        try:
            await asyncio.wait_for(stop.wait(), timeout=settings.outbox_interval_seconds)
        except TimeoutError:
            continue


@asynccontextmanager
async def lifespan(app: FastAPI):
    stop = asyncio.Event()
    task = asyncio.create_task(_drain_loop(stop))
    app.state.stop = stop
    app.state.drain_task = task
    yield
    stop.set()
    task.cancel()
    with suppress(asyncio.CancelledError):
        await task


app = FastAPI(title="AIAT PM Integration Gateway", version="0.1.0", lifespan=lifespan)


@app.get("/health")
async def health() -> dict[str, object]:
    return {"ok": True, "service": "pm-gateway", "orchestrator_url": settings.orchestrator_url}


@app.get("/metrics")
async def metrics() -> Response:
    body = "\n".join(
        (
            "# HELP aiat_pm_gateway_up Gateway process health.",
            "# TYPE aiat_pm_gateway_up gauge",
            "aiat_pm_gateway_up 1",
            "# HELP aiat_pm_gateway_webhook_requests_total Webhooks received by the gateway.",
            "# TYPE aiat_pm_gateway_webhook_requests_total counter",
            f"aiat_pm_gateway_webhook_requests_total {_webhook_requests}",
            "# HELP aiat_pm_gateway_webhook_failures_total Webhook forwarding failures.",
            "# TYPE aiat_pm_gateway_webhook_failures_total counter",
            f"aiat_pm_gateway_webhook_failures_total {_webhook_failures}",
            "# HELP aiat_pm_gateway_drain_failures_total Outbox drain trigger failures.",
            "# TYPE aiat_pm_gateway_drain_failures_total counter",
            f"aiat_pm_gateway_drain_failures_total {_drain_failures}",
            "",
        )
    )
    return Response(content=body, media_type="text/plain; version=0.0.4")


@app.post("/webhooks/{connection_id}", status_code=202)
async def forward_webhook(connection_id: UUID, request: Request) -> JSONResponse:
    global _webhook_requests, _webhook_failures
    _webhook_requests += 1
    declared_length = request.headers.get("content-length")
    if declared_length:
        try:
            if int(declared_length) > settings.webhook_body_max_bytes:
                return JSONResponse(status_code=413, content={"detail": "webhook body is too large"})
        except ValueError:
            return JSONResponse(status_code=400, content={"detail": "invalid content length"})
    chunks: list[bytes] = []
    body_size = 0
    async for chunk in request.stream():
        body_size += len(chunk)
        if body_size > settings.webhook_body_max_bytes:
            return JSONResponse(status_code=413, content={"detail": "webhook body is too large"})
        chunks.append(chunk)
    body = b"".join(chunks)
    delivery_id = (
        request.headers.get("x-youtrack-delivery")
        or request.headers.get("x-delivery-id")
        or request.headers.get("x-github-delivery")
    )
    body_hash = hashlib.sha256(body).hexdigest()
    logger.info(
        "pm_gateway_webhook_received",
        extra={
            "connection_id": str(connection_id),
            "delivery_id": delivery_id,
            "body_sha256": body_hash,
            "provider_auth_header": "x-youtrack-token"
            if request.headers.get("x-youtrack-token") is not None
            else None,
            "cf_ray": request.headers.get("cf-ray"),
        },
    )
    try:
        response = await _forward_to_orchestrator(
            connection_id,
            body,
            _safe_provider_headers(request),
        )
        logger.info(
            "pm_gateway_webhook_forwarded",
            extra={
                "connection_id": str(connection_id),
                "delivery_id": delivery_id,
                "body_sha256": body_hash,
                "origin_status": response.status_code,
                "cf_ray": request.headers.get("cf-ray"),
            },
        )
    except Exception as exc:
        _webhook_failures += 1
        logger.warning("pm_gateway_webhook_forward_error", exc_info=True)
        return JSONResponse(status_code=503, content={"detail": "orchestrator unavailable", "error": str(exc)[:200]})
    if not response.content:
        content: dict[str, object] = {"status": "accepted"}
    else:
        try:
            parsed = response.json()
            content = parsed if isinstance(parsed, dict) else {"result": parsed}
        except ValueError:
            content = {"detail": response.text[:500]}
    return JSONResponse(status_code=response.status_code, content=content)

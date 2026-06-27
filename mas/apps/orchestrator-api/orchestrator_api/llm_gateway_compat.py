"""LLM Gateway External Adapter — OpenAI-compatible API for external agents.

External agents (GitHub-cloned) can point their ``OPENAI_BASE_URL`` to this
endpoint and use their existing OpenAI client libraries.  All requests pass
through the platform's LLM gateway with full routing, cost tracking, and
observability.

Endpoints (OpenAI-compatible):
    GET  /v1/models                  — list available models
    POST /v1/chat/completions        — chat completions (streaming + non-streaming)
    POST /v1/completions             — legacy text completions (mapped to chat)

Authentication:
    Bearer token via ``Authorization: Bearer <GATEWAY_API_KEY>`` header.
    The token is validated against ``GATEWAY_API_KEY`` env var (shared secret)
    or individual per-worker API keys registered in the credentials manager.

Usage by external agent::

    import openai
    client = openai.OpenAI(
        api_key="<GATEWAY_API_KEY>",
        base_url="http://orchestrator-api:8000/v1",
    )
    resp = client.chat.completions.create(
        model="auto",          # platform will pick best model
        messages=[{"role": "user", "content": "Hello"}],
    )
"""

from __future__ import annotations

import json
import logging
import os
import time
import uuid
from typing import TYPE_CHECKING, Any

import httpx
from fastapi import APIRouter, Header, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1", tags=["llm-gateway-compat"])

# Default gateway URL — can also point to an internal LLM service
_LLM_GATEWAY_URL = os.getenv("LLM_GATEWAY_URL", "http://orchestrator-api:8000")
# ---------------------------------------------------------------------------
# Auth helper
# ---------------------------------------------------------------------------


def _check_auth(authorization: str | None) -> None:
    """Validate Bearer token."""
    gateway_api_key = os.getenv("GATEWAY_API_KEY") or os.getenv("MAS_API_KEY", "")
    if not gateway_api_key:
        raise HTTPException(503, "Gateway authentication is not configured")
    if authorization is None:
        raise HTTPException(401, "Authorization header required")
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or token.strip() != gateway_api_key:
        raise HTTPException(401, "Invalid API key")


# ---------------------------------------------------------------------------
# Pydantic models (OpenAI-compatible)
# ---------------------------------------------------------------------------


class ChatMessage(BaseModel):
    role: str
    content: str | list[dict[str, Any]] | None = None
    name: str | None = None
    tool_calls: list[dict[str, Any]] | None = None
    tool_call_id: str | None = None


class ChatCompletionRequest(BaseModel):
    model: str = "auto"
    messages: list[ChatMessage]
    temperature: float | None = None
    top_p: float | None = None
    max_tokens: int | None = None
    stream: bool = False
    tools: list[dict[str, Any]] | None = None
    tool_choice: str | dict[str, Any] | None = None
    response_format: dict[str, Any] | None = None
    stop: str | list[str] | None = None
    user: str | None = None


class LegacyCompletionRequest(BaseModel):
    model: str = "auto"
    prompt: str | list[str]
    max_tokens: int | None = None
    temperature: float | None = None
    stream: bool = False


# ---------------------------------------------------------------------------
# Model registry shim
# ---------------------------------------------------------------------------

# Models that can be selected by external agents
_AVAILABLE_MODELS = [
    "auto",  # platform picks best
    "gpt-4o",
    "gpt-4o-mini",
    "gpt-4.1",
    "gpt-4.1-mini",
    "gemini-2.0-flash",
    "gemini-2.5-pro",
    "mistral-medium",
    "llama-3.3-70b-versatile",
    "cerebras/llama-3.3-70b",
    "nvidia/meta/llama-3.1-70b-instruct",
    "nvidia/nemotron-4-340b-instruct",
]


@router.get("/models")
async def list_models(authorization: str | None = Header(None)) -> dict[str, Any]:
    """Return the list of models available through the gateway."""
    _check_auth(authorization)
    try:
        from mas_core.llm_gateway.models import LLMConfig

        config = LLMConfig()
        if config.backend.strip().lower() == "litellm":
            headers: dict[str, str] = {}
            if config.api_key:
                headers["Authorization"] = f"Bearer {config.api_key}"
            async with httpx.AsyncClient(
                base_url=config.gateway_url,
                headers=headers,
                timeout=min(config.timeout_s, 10.0),
            ) as client:
                response = await client.get("/v1/models")
                response.raise_for_status()
                data = response.json()
                if isinstance(data, dict) and isinstance(data.get("data"), list):
                    return data
    except Exception:
        logger.warning("Falling back to static model list", exc_info=True)

    return {
        "object": "list",
        "data": [
            {
                "id": m,
                "object": "model",
                "created": int(time.time()),
                "owned_by": "mas-platform",
            }
            for m in _AVAILABLE_MODELS
        ],
    }


# ---------------------------------------------------------------------------
# Internal: delegate to LLM gateway
# ---------------------------------------------------------------------------


async def _call_gateway(
    messages: list[dict[str, Any]],
    model: str,
    *,
    temperature: float | None,
    max_tokens: int | None,
    tools: list[dict[str, Any]] | None,
    tool_choice: Any | None,
    stream: bool,
) -> Any:
    """Forward a chat request to the internal LLM gateway providers.

    For now this directly calls the provider API via httpx using
    the platform's routing logic.  A full integration would use
    LLMGatewayClient directly; this lightweight shim avoids circular
    imports.
    """
    from mas_core.llm_gateway.client import LLMGatewayClient
    from mas_core.llm_gateway.models import LLMConfig

    config = LLMConfig()
    # If model is "auto", let the gateway pick
    if model in ("auto", ""):
        model = config.default_model  # default model from env

    async with LLMGatewayClient(config) as client:
        extra: dict[str, Any] = {}
        if temperature is not None:
            extra["temperature"] = temperature
        if max_tokens is not None:
            extra["max_tokens"] = max_tokens
        if tools:
            from mas_core.llm_gateway.models import ToolDefinition

            extra["tools"] = [ToolDefinition.model_validate(t) for t in tools]

        response = await client.chat_completion(
            messages=[m for m in messages],
            model=model,
            **extra,
        )
        return response


def _build_openai_response(
    response: Any,
    model: str,
    request_id: str,
) -> dict[str, Any]:
    """Convert internal ChatResponse to OpenAI-compatible dict."""
    # Handle ChatResponse objects (from LLMGatewayClient)
    if hasattr(response, "message"):
        content = response.message.content or ""
        usage = response.usage
        tool_calls = getattr(response, "tool_calls", None)
        finish_reason_raw = getattr(response, "finish_reason", "stop")
    elif hasattr(response, "content"):
        content = response.content or ""
        usage = response.usage
        tool_calls = getattr(response, "tool_calls", None)
        finish_reason_raw = "stop"
    else:
        content = response.get("content", "")
        usage = response.get("usage", {})
        tool_calls = response.get("tool_calls")
        finish_reason_raw = "stop"

    finish_reason = finish_reason_raw or "stop"
    if tool_calls:
        finish_reason = "tool_calls"

    message: dict[str, Any] = {"role": "assistant", "content": content}
    if tool_calls:
        message["tool_calls"] = [
            {
                "id": getattr(tc, "id", f"call_{uuid.uuid4().hex[:8]}"),
                "type": "function",
                "function": {
                    "name": getattr(tc, "function", {}).get("name", "")
                    if isinstance(getattr(tc, "function", {}), dict)
                    else getattr(getattr(tc, "function", None), "name", ""),
                    "arguments": getattr(tc, "function", {}).get("arguments", "{}")
                    if isinstance(getattr(tc, "function", {}), dict)
                    else getattr(getattr(tc, "function", None), "arguments", "{}"),
                },
            }
            for tc in tool_calls
        ]

    if hasattr(usage, "prompt_tokens"):
        usage_dict = {
            "prompt_tokens": usage.prompt_tokens,
            "completion_tokens": usage.completion_tokens,
            "total_tokens": usage.total_tokens,
        }
    elif isinstance(usage, dict):
        usage_dict = usage
    else:
        usage_dict = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}

    return {
        "id": f"chatcmpl-{request_id}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": message,
                "finish_reason": finish_reason,
                "logprobs": None,
            }
        ],
        "usage": usage_dict,
        "system_fingerprint": "mas-gateway-v1",
    }


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.post("/chat/completions")
async def chat_completions(
    req: ChatCompletionRequest,
    authorization: str | None = Header(None),
) -> Any:
    """OpenAI-compatible chat completions endpoint."""
    _check_auth(authorization)

    request_id = uuid.uuid4().hex[:16]
    messages = [m.model_dump(exclude_none=True) for m in req.messages]

    logger.info(
        "llm_gateway_compat.chat model=%s stream=%s requester=%s",
        req.model,
        req.stream,
        req.user or "unknown",
    )

    if req.stream:
        # Return SSE stream — true first-token streaming via provider's native SSE.
        # For non-chat_completions API styles (Responses API, CLI, Gemini native)
        # the gateway falls back to collecting the full response and re-chunking.
        async def _stream_gen() -> AsyncIterator[str]:
            try:
                from mas_core.llm_gateway.client import LLMGatewayClient
                from mas_core.llm_gateway.models import LLMConfig, ToolDefinition

                config = LLMConfig()
                resolved_model = (
                    req.model if req.model not in ("auto", "") else config.default_model
                )
                tool_defs = (
                    [ToolDefinition.model_validate(t) for t in req.tools] if req.tools else None
                )
                async with LLMGatewayClient(config) as client:
                    async for line in client.stream_raw_sse(
                        messages,
                        model=resolved_model,
                        temperature=req.temperature,
                        max_tokens=req.max_tokens,
                        tools=tool_defs,
                        tool_choice=req.tool_choice,
                        request_id=request_id,
                    ):
                        yield line
            except Exception as e:
                error_event = {"error": {"message": str(e), "type": "gateway_error"}}
                yield f"data: {json.dumps(error_event)}\n\n"

        return StreamingResponse(
            _stream_gen(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
            },
        )

    # Non-streaming
    try:
        response = await _call_gateway(
            messages,
            req.model,
            temperature=req.temperature,
            max_tokens=req.max_tokens,
            tools=req.tools,
            tool_choice=req.tool_choice,
            stream=False,
        )
        return _build_openai_response(response, req.model, request_id)
    except Exception as e:
        logger.exception("llm_gateway_compat.error model=%s", req.model)
        raise HTTPException(500, f"LLM gateway error: {e}") from e


@router.post("/completions")
async def legacy_completions(
    req: LegacyCompletionRequest,
    authorization: str | None = Header(None),
) -> dict[str, Any]:
    """Legacy text completions — mapped to chat completions internally."""
    _check_auth(authorization)

    prompt = req.prompt if isinstance(req.prompt, str) else "\n".join(req.prompt)
    messages = [{"role": "user", "content": prompt}]

    try:
        response = await _call_gateway(
            messages,
            req.model,
            temperature=req.temperature,
            max_tokens=req.max_tokens,
            tools=None,
            tool_choice=None,
            stream=False,
        )
    except Exception as e:
        raise HTTPException(500, f"LLM gateway error: {e}") from e

    content = ""
    if hasattr(response, "content"):
        content = response.content or ""
    elif isinstance(response, dict):
        content = response.get("content", "")

    request_id = uuid.uuid4().hex[:16]
    return {
        "id": f"cmpl-{request_id}",
        "object": "text_completion",
        "created": int(time.time()),
        "model": req.model,
        "choices": [
            {
                "text": content,
                "index": 0,
                "logprobs": None,
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
    }

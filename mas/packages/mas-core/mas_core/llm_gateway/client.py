"""LLMGatewayClient — async HTTP client targeting an OpenAI-compatible provider.

Usage
-----
::

    config = LLMConfig()
    client = LLMGatewayClient(config)
    async with client:
        response = await client.chat_completion(
            messages=[
                {"role": "system", "content": "You are a helpful assistant."},
                {"role": "user", "content": "What is 2 + 2?"},
            ],
            tools=[...],   # optional
        )
    print(response.text)
    print(response.usage.total_tokens)

Retry behaviour
---------------
429 (rate limited) and 5xx responses are retried with exponential backoff.
The retry policy is configured in ``LLMConfig`` (max_retries, min/max wait).
``httpx.TimeoutException`` is also retried.

Token tracking
--------------
Each ``chat_completion`` call returns a ``ChatResponse`` with ``usage`` populated.
Callers should pass ``response.usage`` to ``BudgetTracker.consume_llm_call()``
for cost tracking.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

import httpx

from .models import (
    ChatMessage,
    ChatResponse,
    LLMConfig,
    ToolCall,
    ToolCallFunction,
    ToolDefinition,
    UsageStats,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class LLMGatewayError(Exception):
    """Non-retryable error from the LLM gateway."""

    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(f"LLM gateway error {status_code}: {detail}")
        self.status_code = status_code
        self.detail = detail


class LLMRateLimited(LLMGatewayError):
    """429 — rate limited. Retried automatically by the client."""


# ---------------------------------------------------------------------------
# LLMGatewayClient
# ---------------------------------------------------------------------------


class LLMGatewayClient:
    """Async client for the OpenAI-compatible chat completions endpoint.

    Parameters
    ----------
    config:
        ``LLMConfig`` instance (reads env vars by default).
    """

    def __init__(self, config: LLMConfig | None = None) -> None:
        self._config = config or LLMConfig()
        self._http: httpx.AsyncClient | None = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        headers: dict[str, str] = {"Content-Type": "application/json"}
        if self._config.api_key:
            headers["Authorization"] = f"Bearer {self._config.api_key}"
        self._http = httpx.AsyncClient(
            base_url=self._config.gateway_url,
            headers=headers,
            timeout=self._config.timeout_s,
        )

    async def stop(self) -> None:
        if self._http is not None:
            await self._http.aclose()
            self._http = None

    async def __aenter__(self) -> "LLMGatewayClient":
        await self.start()
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.stop()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def chat_completion(
        self,
        messages: list[dict[str, Any]],
        *,
        model: str | None = None,
        tools: list[ToolDefinition] | None = None,
        tool_choice: str | dict[str, Any] = "auto",
        max_tokens: int | None = None,
        temperature: float = 0.7,
        stream: bool = False,
    ) -> ChatResponse:
        """Send a chat completion request with automatic retry on 429/5xx.

        Parameters
        ----------
        messages:
            Conversation history in OpenAI format (list of dicts with
            ``role`` and ``content``).
        model:
            Override the default model from ``LLMConfig``.
        tools:
            Tool definitions to pass to the LLM (enables tool use).
        tool_choice:
            ``"auto"`` (default), ``"none"``, or a specific tool dict.
        max_tokens:
            Hard cap on completion tokens. None = provider default.
        temperature:
            Sampling temperature.
        stream:
            If True, parse server-sent event chunks from the streaming API.

        Returns
        -------
        ChatResponse
            Normalised response with assistant message, tool calls, and usage.
        """
        client = self._require_http()
        payload: dict[str, Any] = {
            "model": model or self._config.default_model,
            "messages": messages,
            "temperature": temperature,
        }
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens
        if tools:
            payload["tools"] = [t.model_dump() for t in tools]
            payload["tool_choice"] = tool_choice
        if stream:
            payload["stream"] = True
            payload["stream_options"] = {"include_usage": True}

        last_exc: Exception | None = None
        wait_s = self._config.retry_min_wait_s
        max_retries = self._config.max_retries

        for attempt in range(max_retries + 1):
            try:
                if stream:
                    async with client.stream(
                        "POST",
                        "/v1/chat/completions",
                        json=payload,
                    ) as response:
                        if response.status_code == 200:
                            return await self._parse_stream_response(response)
                        detail = (await response.aread()).decode("utf-8", errors="replace")
                        if self._is_retryable_status(response.status_code):
                            last_exc = (
                                LLMRateLimited(response.status_code, detail)
                                if response.status_code == 429
                                else LLMGatewayError(response.status_code, detail)
                            )
                            self._log_retry(response.status_code, attempt, max_retries, wait_s)
                            if attempt < max_retries:
                                await asyncio.sleep(min(wait_s, self._config.retry_max_wait_s))
                                wait_s *= 2
                                continue
                            break
                        raise LLMGatewayError(response.status_code, detail)

                response = await client.post("/v1/chat/completions", json=payload)
            except httpx.TimeoutException as exc:
                last_exc = exc
                if attempt < max_retries:
                    await asyncio.sleep(min(wait_s, self._config.retry_max_wait_s))
                    wait_s *= 2
                continue

            if response.status_code == 200:
                return self._parse_response(response.json())

            if self._is_retryable_status(response.status_code):
                last_exc = (
                    LLMRateLimited(response.status_code, response.text)
                    if response.status_code == 429
                    else LLMGatewayError(response.status_code, response.text)
                )
                self._log_retry(response.status_code, attempt, max_retries, wait_s)
                if attempt < max_retries:
                    await asyncio.sleep(min(wait_s, self._config.retry_max_wait_s))
                    wait_s *= 2
                continue

            # Non-retryable client error (4xx other than 429)
            raise LLMGatewayError(response.status_code, response.text)

        raise last_exc or LLMGatewayError(0, "Unknown error after retries exhausted")

    # ------------------------------------------------------------------
    # Response parsing
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_response(data: dict[str, Any]) -> ChatResponse:
        """Parse an OpenAI-format response dict into a ``ChatResponse``."""
        choice = data.get("choices", [{}])[0]
        raw_message = choice.get("message", {})
        finish_reason = choice.get("finish_reason", "stop")
        tool_calls = LLMGatewayClient._parse_tool_calls(raw_message.get("tool_calls") or [])
        usage = LLMGatewayClient._parse_usage(data.get("usage", {}))

        message = ChatMessage(
            role=raw_message.get("role", "assistant"),
            content=raw_message.get("content"),
            tool_calls=tool_calls if tool_calls else None,
        )

        return ChatResponse(
            response_id=data.get("id", ""),
            model=data.get("model", ""),
            finish_reason=finish_reason,
            message=message,
            tool_calls=tool_calls,
            usage=usage,
        )

    @staticmethod
    def _parse_tool_calls(raw_tool_calls: list[dict[str, Any]]) -> list[ToolCall]:
        """Parse OpenAI tool calls into strongly typed models."""
        tool_calls: list[ToolCall] = []
        for tc in raw_tool_calls:
            fn = tc.get("function", {})
            tool_calls.append(
                ToolCall(
                    id=tc.get("id", ""),
                    type=tc.get("type", "function"),
                    function=ToolCallFunction(
                        name=fn.get("name", ""),
                        arguments=fn.get("arguments", "{}"),
                    ),
                )
            )
        return tool_calls

    @staticmethod
    def _parse_usage(raw_usage: dict[str, Any]) -> UsageStats:
        """Parse provider usage payload into UsageStats."""
        prompt_tokens = int(raw_usage.get("prompt_tokens", 0) or 0)
        completion_tokens = int(raw_usage.get("completion_tokens", 0) or 0)
        total_tokens = int(raw_usage.get("total_tokens", 0) or 0)
        if total_tokens == 0:
            total_tokens = prompt_tokens + completion_tokens
        return UsageStats(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
        )

    async def _parse_stream_response(self, response: httpx.Response) -> ChatResponse:
        """Parse streaming SSE chunks from /v1/chat/completions."""
        response_id = ""
        model = ""
        role = "assistant"
        finish_reason = "stop"
        content_parts: list[str] = []
        usage = UsageStats()
        tool_calls_by_idx: dict[int, dict[str, Any]] = {}

        async for line in response.aiter_lines():
            if not line:
                continue
            if not line.startswith("data:"):
                continue
            data_raw = line[5:].strip()
            if not data_raw or data_raw == "[DONE]":
                if data_raw == "[DONE]":
                    break
                continue
            try:
                chunk = json.loads(data_raw)
            except json.JSONDecodeError:
                logger.debug("Skipping malformed SSE chunk: %s", data_raw)
                continue

            if not response_id:
                response_id = chunk.get("id", "")
            if not model:
                model = chunk.get("model", "")
            if "usage" in chunk and chunk["usage"]:
                usage = self._parse_usage(chunk["usage"])

            for choice in chunk.get("choices", []):
                if choice.get("finish_reason") is not None:
                    finish_reason = choice["finish_reason"]
                delta = choice.get("delta", {}) or {}
                if delta.get("role"):
                    role = delta["role"]
                if delta.get("content"):
                    content_parts.append(delta["content"])
                for tc in delta.get("tool_calls") or []:
                    idx = int(tc.get("index", 0))
                    existing = tool_calls_by_idx.setdefault(
                        idx,
                        {
                            "id": "",
                            "type": "function",
                            "function": {"name": "", "arguments": ""},
                        },
                    )
                    if tc.get("id"):
                        existing["id"] = tc["id"]
                    if tc.get("type"):
                        existing["type"] = tc["type"]
                    fn = tc.get("function") or {}
                    if fn.get("name"):
                        existing["function"]["name"] += fn["name"]
                    if fn.get("arguments"):
                        existing["function"]["arguments"] += fn["arguments"]

        raw_tool_calls = [tool_calls_by_idx[idx] for idx in sorted(tool_calls_by_idx.keys())]
        tool_calls = self._parse_tool_calls(raw_tool_calls)
        message = ChatMessage(
            role=role,
            content=("".join(content_parts) or None),
            tool_calls=tool_calls if tool_calls else None,
        )
        return ChatResponse(
            response_id=response_id,
            model=model,
            finish_reason=finish_reason,
            message=message,
            tool_calls=tool_calls,
            usage=usage,
        )

    @staticmethod
    def _is_retryable_status(status_code: int) -> bool:
        return status_code == 429 or status_code >= 500

    @staticmethod
    def _log_retry(status_code: int, attempt: int, max_retries: int, wait_s: float) -> None:
        logger.warning(
            "LLM gateway %d (attempt %d/%d), retrying in %.1fs",
            status_code,
            attempt + 1,
            max_retries + 1,
            wait_s,
        )

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _require_http(self) -> httpx.AsyncClient:
        if self._http is None:
            raise RuntimeError(
                "LLMGatewayClient not started. "
                "Use 'async with client:' or call 'await client.start()' first."
            )
        return self._http

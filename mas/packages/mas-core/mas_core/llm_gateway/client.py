"""LLMGatewayClient — scalable async HTTP client for multiple LLM providers.

Supports any backend registered in the ``MODEL_REGISTRY`` — OpenAI, Zen
free-tier, Ollama, or CLI-based models — with automatic provider routing,
API-style selection, and per-provider auth.

Usage
-----
::

    config = LLMConfig()
    client = LLMGatewayClient(config)
    async with client:
        # Auto-routes to the correct provider + API style
        response = await client.chat_completion(
            messages=[{"role": "user", "content": "Hello"}],
            model="big-pickle",          # Zen free chat-completions
        )
        response2 = await client.chat_completion(
            messages=[{"role": "user", "content": "Analyse this"}],
            model="gpt-5-nano",          # Zen free responses API
        )

Provider routing
----------------
When a model ID is found in ``MODEL_REGISTRY``, the client uses that entry's
endpoint, API style, and provider headers.  Unknown model IDs fall back to
the default ``LLMConfig.gateway_url`` + ``/v1/chat/completions``.

API styles
----------
chat_completions
    Standard OpenAI ``/v1/chat/completions`` format.
responses
    OpenAI Responses API (input/output blocks).
cli
    Subprocess invocation (for local models like llama.cpp).

Retry behaviour
---------------
429 (rate limited) and 5xx responses are retried with exponential backoff.
The retry policy is configured in ``LLMConfig`` (max_retries, min/max wait).
``httpx.TimeoutException`` is also retried.

Token tracking
--------------
Each ``chat_completion`` call returns a ``ChatResponse`` with ``usage``
populated.  Callers should pass ``response.usage`` to
``BudgetTracker.consume_llm_call()`` for cost tracking.
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
from .providers import MODEL_REGISTRY, ApiStyle, ModelEntry

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
    """Scalable async client that routes to multiple LLM providers.

    When a model is found in ``MODEL_REGISTRY``, the client uses the
    registered endpoint, API style, and provider-level auth / headers.
    Unknown models fall back to the default ``LLMConfig`` settings
    (single OpenAI-compatible endpoint).

    Parameters
    ----------
    config:
        ``LLMConfig`` instance (reads env vars by default).
    registry:
        Optional custom ``ModelRegistry``.  Defaults to the global
        ``MODEL_REGISTRY`` singleton.
    """

    def __init__(
        self,
        config: LLMConfig | None = None,
        *,
        registry: Any | None = None,
    ) -> None:
        self._config = config or LLMConfig()
        self._registry = registry if registry is not None else MODEL_REGISTRY
        self._http: httpx.AsyncClient | None = None
        # Per-provider HTTP clients (created lazily on first use)
        self._provider_clients: dict[str, httpx.AsyncClient] = {}

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
        for pc in self._provider_clients.values():
            await pc.aclose()
        self._provider_clients.clear()

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
        resolved_model = model or self._config.default_model
        entry = self._registry.get(resolved_model)

        # Dispatch to the correct API style
        if entry is not None and entry.api_style == ApiStyle.RESPONSES:
            return await self._call_responses_api(
                entry=entry,
                messages=messages,
                model=resolved_model,
                max_tokens=max_tokens,
                temperature=temperature,
            )
        if entry is not None and entry.api_style == ApiStyle.CLI:
            return await self._call_cli(
                entry=entry,
                messages=messages,
                model=resolved_model,
            )

        # Default: chat_completions style
        client, endpoint = self._resolve_http_client_and_endpoint(entry)
        payload: dict[str, Any] = {
            "model": resolved_model,
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
                        endpoint,
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

                response = await client.post(endpoint, json=payload)
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

    # ------------------------------------------------------------------
    # Provider-aware HTTP client + endpoint resolution
    # ------------------------------------------------------------------

    def _resolve_http_client_and_endpoint(
        self, entry: ModelEntry | None,
    ) -> tuple[httpx.AsyncClient, str]:
        """Return (httpx_client, endpoint_path) for a model.

        When the model has a registered provider, we create (or reuse) a
        provider-specific ``httpx.AsyncClient`` with the correct auth +
        headers.  Otherwise we fall back to the default client with
        ``/v1/chat/completions``.
        """
        if entry is None:
            return self._require_http(), "/v1/chat/completions"

        provider = self._registry.get_provider(entry.provider)
        if provider is None:
            return self._require_http(), "/v1/chat/completions"

        pid = provider.provider_id
        if pid not in self._provider_clients:
            api_key = provider.resolve_api_key()
            headers: dict[str, str] = {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}",
                **provider.extra_headers,
            }
            self._provider_clients[pid] = httpx.AsyncClient(
                headers=headers,
                timeout=self._config.timeout_s,
            )

        return self._provider_clients[pid], entry.endpoint

    # ------------------------------------------------------------------
    # Responses API (gpt-5-nano, etc.)
    # ------------------------------------------------------------------

    async def _call_responses_api(
        self,
        *,
        entry: ModelEntry,
        messages: list[dict[str, Any]],
        model: str,
        max_tokens: int | None = None,
        temperature: float = 0.7,
    ) -> ChatResponse:
        """Call a Responses-API endpoint and normalise the result.

        Request format::

            {
                "model": "gpt-5-nano",
                "input": [
                    {"role": "system", "content": "..."},
                    {"role": "user",   "content": "..."}
                ],
                "stream": false
            }
        """
        client, endpoint = self._resolve_http_client_and_endpoint(entry)

        # Build responses-API payload (simpler than the typed-block format
        # used by the official SDK — Zen accepts plain strings too)
        input_blocks: list[dict[str, Any]] = []
        for msg in messages:
            role = msg.get("role", "user")
            content = msg.get("content", "")
            input_blocks.append({"role": role, "content": content or ""})

        payload: dict[str, Any] = {
            "model": model,
            "input": input_blocks,
            "stream": False,
        }
        if temperature is not None:
            payload["temperature"] = temperature
        if max_tokens is not None:
            payload["max_output_tokens"] = max_tokens

        last_exc: Exception | None = None
        wait_s = self._config.retry_min_wait_s
        max_retries = self._config.max_retries

        for attempt in range(max_retries + 1):
            try:
                response = await client.post(endpoint, json=payload)
            except httpx.TimeoutException as exc:
                last_exc = exc
                if attempt < max_retries:
                    await asyncio.sleep(min(wait_s, self._config.retry_max_wait_s))
                    wait_s *= 2
                continue

            if response.status_code == 200:
                return self._parse_responses_api(response.json(), model=model)

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
            raise LLMGatewayError(response.status_code, response.text)

        raise last_exc or LLMGatewayError(0, "Unknown error after retries exhausted")

    @staticmethod
    def _parse_responses_api(
        data: dict[str, Any], *, model: str = "",
    ) -> ChatResponse:
        """Parse a Responses-API JSON body into ``ChatResponse``.

        Handles ``output_text`` shorthand **and** nested ``output`` array.
        """
        text = ""
        if isinstance(data.get("output_text"), str) and data["output_text"]:
            text = data["output_text"]
        else:
            output = data.get("output") or []
            chunks: list[str] = []
            for item in output:
                content = item.get("content") or []
                for part in content:
                    if isinstance(part.get("text"), str):
                        chunks.append(part["text"])
                    if isinstance(part.get("output_text"), str):
                        chunks.append(part["output_text"])
            text = "".join(chunks).strip()

        raw_usage = data.get("usage", {})
        # Responses API uses input_tokens / output_tokens naming
        normalised = {
            "prompt_tokens": raw_usage.get("input_tokens", raw_usage.get("prompt_tokens", 0)),
            "completion_tokens": raw_usage.get("output_tokens", raw_usage.get("completion_tokens", 0)),
            "total_tokens": raw_usage.get("total_tokens", 0),
        }
        usage = LLMGatewayClient._parse_usage(normalised)

        message = ChatMessage(role="assistant", content=text or None)
        return ChatResponse(
            response_id=data.get("id", ""),
            model=model,
            finish_reason="stop",
            message=message,
            usage=usage,
        )

    # ------------------------------------------------------------------
    # CLI execution (local models like llama.cpp)
    # ------------------------------------------------------------------

    async def _call_cli(
        self,
        *,
        entry: ModelEntry,
        messages: list[dict[str, Any]],
        model: str,
    ) -> ChatResponse:
        """Execute a CLI-based model via subprocess.

        Two modes are supported:

        **stdin mode** (default)
            The conversation is formatted as text and piped to the
            process's stdin.  Used by llama.cpp, Ollama CLI, etc.

        **flag mode** (when ``entry.cli_prompt_flag`` is set)
            The prompt is passed as a CLI argument (e.g. ``-p "…"``).
            If ``entry.cli_model_flag`` is also set, the native model
            name is injected (e.g. ``--model gpt-5-mini``).  Used by
            the GitHub Copilot CLI.
        """
        # ---- Build the prompt string from messages ----
        prompt_parts: list[str] = []
        for msg in messages:
            role = msg.get("role", "user")
            content = msg.get("content", "")
            if role == "system":
                prompt_parts.append(f"[System] {content}")
            elif role == "user":
                prompt_parts.append(f"[User] {content}")
            elif role == "assistant":
                prompt_parts.append(f"[Assistant] {content}")
        prompt = "\n".join(prompt_parts)

        # ---- Build command ----
        cmd: list[str] = [entry.endpoint, *entry.cli_args]

        use_stdin = True
        if entry.cli_prompt_flag:
            # Flag mode — prompt goes as a CLI argument
            cmd.extend([entry.cli_prompt_flag, prompt])
            use_stdin = False

        if entry.cli_model_flag:
            # Inject native model name (strip provider prefix if present)
            native_name = entry.extra.get("cli_model_name", model)
            cmd.extend([entry.cli_model_flag, native_name])

        logger.info("CLI model %s: running %s (stdin=%s)", model, cmd[0], use_stdin)

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.PIPE if use_stdin else None,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        if use_stdin:
            stdout, stderr = await proc.communicate(prompt.encode("utf-8"))
        else:
            stdout, stderr = await proc.communicate()

        text = stdout.decode("utf-8", errors="replace").strip()

        if proc.returncode != 0:
            err = stderr.decode("utf-8", errors="replace").strip()
            raise LLMGatewayError(
                proc.returncode or 1,
                f"CLI model {model} failed: {err}",
            )

        message = ChatMessage(role="assistant", content=text or None)
        return ChatResponse(
            model=model,
            finish_reason="stop",
            message=message,
            usage=UsageStats(),
        )

    # ------------------------------------------------------------------
    # Convenience
    # ------------------------------------------------------------------

    def list_models(self, provider: str | None = None) -> list[ModelEntry]:
        """Return registered models from the registry."""
        return self._registry.list_models(provider)

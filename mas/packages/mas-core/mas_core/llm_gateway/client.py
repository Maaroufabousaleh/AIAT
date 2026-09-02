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
            model="north-mini-code-free",  # Zen free chat-completions
        )

Agent convenience API
---------------------
::

    async with LLMGatewayClient() as client:
        # One-liner: no message boilerplate, auto-selects the best free model
        text = await client.ask("Summarise this document: ...")

        # With a task hint — picks a model good at code generation
        text = await client.ask("Write a Python sort function",
                                task="code-generation")

        # Stateful multi-turn conversation
        async with client.chat(system="You are a helpful assistant.") as conv:
            r1 = await conv.send("What is the capital of France?")
            r2 = await conv.send("And what is its population?")

        # Automatic fallback cascade: tries ranked models until one succeeds
        response = await client.chat_completion_with_fallback(
            messages=[{"role": "user", "content": "Explain quantum computing"}],
            task="reasoning",
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
Transient 408/409/412/425/429/5xx responses are retried with exponential backoff.
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
import re
import time as _time
from typing import TYPE_CHECKING, Any

import httpx

from .audit import AuditEvent, AuditLevel, AuditLog, fingerprint_messages
from .metrics import MetricsCollector
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
from .rate_limits import RateLimitTracker

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

logger = logging.getLogger(__name__)

_OPENAI_TOOL_NAME_RE = re.compile(r"^[a-zA-Z0-9_-]+$")

# Keep transient provider failures explicit and deterministic across the
# normal, streaming, and fallback dispatch paths.  Credential/permission and
# malformed-request 4xx responses remain permanent and are never blindly
# retried.  409/412 are treated as retryable only at this transport layer;
# callers still own any provider-state refresh or reconciliation semantics.
RETRYABLE_LLM_STATUS_CODES = frozenset({408, 409, 412, 425, 429, 500, 502, 503, 504})


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
# Stateful conversation context manager (forward-declared before the client
# so return-type annotations in LLMGatewayClient.chat() resolve cleanly)
# ---------------------------------------------------------------------------


class _ConversationContext:
    """Async context manager for stateful multi-turn conversations.

    Do not instantiate directly — use ``LLMGatewayClient.chat()``
    instead::

        async with client.chat(system="You are a helpful assistant.") as conv:
            reply1 = await conv.send("Hello!")
            reply2 = await conv.send("Tell me more.")
            print(conv.history)   # full conversation history
    """

    def __init__(
        self,
        client: LLMGatewayClient,
        *,
        system: str | None = None,
        model: str | None = None,
        task: str | None = None,
        search_grounding: bool = False,
        temperature: float = 0.7,
        max_tokens: int | None = None,
    ) -> None:
        self._client = client
        self._model = model
        self._task = task
        self._search_grounding = search_grounding
        self._temperature = temperature
        self._max_tokens = max_tokens
        self.history: list[dict[str, Any]] = []
        if system:
            self.history.append({"role": "system", "content": system})

    async def __aenter__(self) -> _ConversationContext:
        return self

    async def __aexit__(self, *_: object) -> None:
        pass  # history is discarded; client lifecycle is managed externally

    async def send(
        self,
        message: str,
        *,
        tools: list[ToolDefinition] | None = None,
        tool_choice: str | dict[str, Any] = "auto",
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> str:
        """Send a message and receive the assistant's text reply.

        The message is appended to ``self.history`` and the response is
        added automatically, so the full conversation is preserved across
        turns.

        Parameters
        ----------
        message:
            The user's message text.
        tools:
            Optional tool definitions for this turn.
        tool_choice:
            Tool choice strategy.
        temperature:
            Override temperature for this turn only.
        max_tokens:
            Override max tokens for this turn only.

        Returns
        -------
        str
            The assistant's text response.
        """
        self.history.append({"role": "user", "content": message})

        # Resolve model (use auto-selection if not specified)
        model = self._model or self._client._auto_select_model(
            task=self._task,
            needs_tools=bool(tools),
        )

        resp = await self._client.chat_completion(
            messages=self.history,
            model=model,
            tools=tools,
            tool_choice=tool_choice,
            search_grounding=self._search_grounding,
            temperature=temperature if temperature is not None else self._temperature,
            max_tokens=max_tokens if max_tokens is not None else self._max_tokens,
        )

        # Append the assistant's response to history
        self.history.append(
            {
                "role": "assistant",
                "content": resp.text or "",
            }
        )
        return resp.text

    async def send_with_response(
        self,
        message: str,
        *,
        tools: list[ToolDefinition] | None = None,
        tool_choice: str | dict[str, Any] = "auto",
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> ChatResponse:
        """Like ``send()`` but returns the full ``ChatResponse`` object.

        Useful when you need access to tool calls or usage statistics.
        """
        self.history.append({"role": "user", "content": message})

        model = self._model or self._client._auto_select_model(
            task=self._task,
            needs_tools=bool(tools),
        )

        resp = await self._client.chat_completion(
            messages=self.history,
            model=model,
            tools=tools,
            tool_choice=tool_choice,
            search_grounding=self._search_grounding,
            temperature=temperature if temperature is not None else self._temperature,
            max_tokens=max_tokens if max_tokens is not None else self._max_tokens,
        )

        self.history.append(
            {
                "role": "assistant",
                "content": resp.text or "",
            }
        )
        return resp

    def add_tool_result(self, tool_call_id: str, result: str) -> None:
        """Append a tool result message to the conversation history.

        Call this after executing a tool requested by the assistant to
        feed the result back into the conversation::

            resp = await conv.send_with_response("Analyse the codebase")
            for tc in resp.tool_calls:
                output = execute_tool(tc)
                conv.add_tool_result(tc.id, output)
            # Next send() will see the tool results in history
        """
        self.history.append(
            {
                "role": "tool",
                "tool_call_id": tool_call_id,
                "content": result,
            }
        )

    def reset(self, keep_system: bool = True) -> None:
        """Clear the conversation history.

        Parameters
        ----------
        keep_system:
            When ``True`` (default), preserve the system prompt at
            position 0 (if one was set).
        """
        if keep_system and self.history and self.history[0]["role"] == "system":
            self.history = [self.history[0]]
        else:
            self.history = []


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
        audit_level: AuditLevel = AuditLevel.STANDARD,
        audit_log: AuditLog | None = None,
        metrics: MetricsCollector | None = None,
        rate_limit_tracker: RateLimitTracker | None = None,
    ) -> None:
        self._config = config or LLMConfig()
        self._registry = registry if registry is not None else MODEL_REGISTRY
        self._http: httpx.AsyncClient | None = None
        # Per-provider HTTP clients (created lazily on first use)
        self._provider_clients: dict[str, httpx.AsyncClient] = {}

        # ── Observability ────────────────────────────────────────────
        self.audit_log: AuditLog = audit_log or AuditLog(level=audit_level)
        self.metrics: MetricsCollector = metrics or MetricsCollector()
        self.rate_limits: RateLimitTracker = rate_limit_tracker or RateLimitTracker()
        self._smart_router: Any | None = None  # lazy init
        self._model_selector: Any | None = None  # lazy init

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

    async def __aenter__(self) -> LLMGatewayClient:
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
        search_grounding: bool = False,
        max_tokens: int | None = None,
        temperature: float = 0.7,
        stream: bool = False,
        max_retries: int | None = None,
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

        # ── Audit pre-flight ─────────────────────────────────────────
        t0 = _time.time()
        audit_evt = AuditEvent(
            model=resolved_model,
            message_count=len(messages),
            tool_count=len(tools) if tools else 0,
            max_tokens_requested=max_tokens,
            temperature=temperature,
            stream=stream,
        )
        if self.audit_log.level >= AuditLevel.STANDARD and tools:
            audit_evt.tool_names = [t.function.name for t in tools]
        if self.audit_log.level >= AuditLevel.FULL:
            audit_evt.content_fingerprint = fingerprint_messages(messages)

        retry_counter = [0]

        try:
            resp = await self._dispatch(
                messages=messages,
                resolved_model=resolved_model,
                tools=tools,
                tool_choice=tool_choice,
                search_grounding=search_grounding,
                max_tokens=max_tokens,
                temperature=temperature,
                stream=stream,
                _audit_evt=audit_evt,
                _retry_counter_ref=retry_counter,
                _max_retries=max_retries,
            )

            # ── Post-call audit + metrics recording ──────────────────
            latency = _time.time() - t0
            self._record_success(
                audit_evt,
                resp,
                latency,
                retry_counter[0],
            )
            return resp

        except LLMRateLimited as exc:
            latency = _time.time() - t0
            self._record_failure(
                audit_evt,
                "rate_limited",
                exc.status_code,
                latency,
                str(exc),
            )
            raise
        except LLMGatewayError as exc:
            latency = _time.time() - t0
            self._record_failure(
                audit_evt,
                "error",
                exc.status_code,
                latency,
                str(exc),
            )
            raise
        except httpx.TransportError:
            latency = _time.time() - t0
            self._record_failure(
                audit_evt,
                "timeout",
                0,
                latency,
                "timeout",
            )
            raise
        except Exception as exc:
            latency = _time.time() - t0
            self._record_failure(
                audit_evt,
                "error",
                0,
                latency,
                str(exc),
            )
            raise

    # ------------------------------------------------------------------
    # Streaming: true first-token SSE proxy
    # ------------------------------------------------------------------

    async def stream_raw_sse(
        self,
        messages: list[dict[str, Any]],
        *,
        model: str | None = None,
        temperature: float = 0.7,
        max_tokens: int | None = None,
        tools: list[ToolDefinition] | None = None,
        tool_choice: str | dict[str, Any] = "auto",
        request_id: str = "",
    ) -> AsyncIterator[str]:
        """Yield raw OpenAI-compatible SSE data strings for a streaming completion.

        For ``chat_completions``-style providers this is a true first-token
        streaming path — ``data: ...`` lines are forwarded to the caller as
        the provider generates them, so time-to-first-token equals the actual
        provider TTFT.

        For non-streamable API styles (Responses API, CLI, thinking chains)
        the full response is collected first and re-chunked; TTFT equals the
        full round-trip for those paths.
        """
        import uuid as _uuid

        from .providers.api.openrouter import (
            OPENROUTER_FREE_ROUTER_MODEL_ID,
            OPENROUTER_FREE_ROUTER_WIRE_MODEL,
            ensure_free_openrouter_model,
        )

        rid = request_id or _uuid.uuid4().hex[:16]
        resolved_model = model or self._config.default_model

        force_legacy_backend = self._uses_litellm_backend() and resolved_model.startswith("legacy:")
        if force_legacy_backend:
            resolved_model = resolved_model.removeprefix("legacy:")

        # ── Thinking chain / non-streamable shortcut ─────────────────
        if resolved_model.startswith("gemma-think"):
            resp = await self.chat_completion(
                messages=messages,
                model=resolved_model,
                temperature=temperature,
                max_tokens=max_tokens,
            )
            async for line in self._chat_response_to_sse(resp, rid):
                yield line
            return

        if self._uses_litellm_backend() and not force_legacy_backend:
            async for line in self._stream_default_raw_sse(
                messages=messages,
                model=resolved_model,
                temperature=temperature,
                max_tokens=max_tokens,
                tools=tools,
                tool_choice=tool_choice,
            ):
                yield line
            return

        # ── OpenRouter wire model normalisation ───────────────────────
        if resolved_model == OPENROUTER_FREE_ROUTER_WIRE_MODEL:
            resolved_model = OPENROUTER_FREE_ROUTER_MODEL_ID

        # ── Pool / entry resolution ───────────────────────────────────
        entry, pool = self._registry.resolve_pool(resolved_model)
        if pool is not None and entry is None:
            raise LLMRateLimited(
                429,
                f"All models in pool '{resolved_model}' are rate-limited.",
            )

        concrete_model = entry.model_id if entry is not None else resolved_model
        wire_model = (
            entry.extra.get("api_model_name", concrete_model)
            if entry is not None
            else concrete_model
        )
        if entry is not None and entry.provider == "openrouter":
            try:
                wire_model = ensure_free_openrouter_model(wire_model)
            except ValueError as exc:
                raise LLMGatewayError(400, str(exc)) from exc

        # ── Non-chat_completions styles → collect + re-chunk ─────────
        if entry is not None and entry.api_style != ApiStyle.CHAT_COMPLETIONS:
            resp = await self.chat_completion(
                messages=messages,
                model=resolved_model,
                temperature=temperature,
                max_tokens=max_tokens,
                tools=tools,
                tool_choice=tool_choice,
            )
            async for line in self._chat_response_to_sse(resp, rid):
                yield line
            return

        # ── chat_completions style — true SSE streaming ───────────────
        payload: dict[str, Any] = {
            "model": wire_model,
            "messages": messages,
            "temperature": temperature,
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens
        if tools:
            payload["tools"] = [t.model_dump() for t in tools]
            payload["tool_choice"] = tool_choice

        http_client, endpoint = self._resolve_http_client_and_endpoint(entry)
        wait_s = self._config.retry_min_wait_s
        max_retries = self._config.max_retries
        last_exc: Exception | None = None

        for attempt in range(max_retries + 1):
            try:
                async with http_client.stream("POST", endpoint, json=payload) as response:
                    if response.status_code == 200:
                        async for raw_line in response.aiter_lines():
                            if raw_line:
                                yield raw_line + "\n\n"
                        if pool is not None:
                            # Token counts aren't available without parsing;
                            # record a nominal request so pool headroom is tracked.
                            pool.record_request(concrete_model, 0)
                        return
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
                    raise LLMGatewayError(response.status_code, detail)
            except httpx.TransportError as exc:
                last_exc = exc
                if attempt < max_retries:
                    await asyncio.sleep(min(wait_s, self._config.retry_max_wait_s))
                    wait_s *= 2
                continue

        if last_exc is not None:
            self._record_stream_failure(
                concrete_model,
                last_exc,
                provider=entry.provider if entry is not None else None,
            )
        raise last_exc or LLMGatewayError(0, "Unknown error after retries exhausted")

    @staticmethod
    async def _chat_response_to_sse(
        resp: ChatResponse,
        request_id: str,
    ) -> AsyncIterator[str]:
        """Yield OpenAI-compatible SSE data lines from a collected ChatResponse.

        Used as a fallback for non-streamable API styles (Responses API, CLI,
        Gemini native, thinking chains).
        """
        import json as _json
        import time as _t

        content = resp.message.content or ""
        model = resp.model or ""
        chunk_size = 20
        for i in range(0, max(1, len(content)), chunk_size):
            chunk_content = content[i : i + chunk_size]
            chunk = {
                "id": f"chatcmpl-{request_id}",
                "object": "chat.completion.chunk",
                "created": int(_t.time()),
                "model": model,
                "choices": [
                    {
                        "index": 0,
                        "delta": {"role": "assistant", "content": chunk_content}
                        if i == 0
                        else {"content": chunk_content},
                        "finish_reason": None,
                    }
                ],
            }
            yield f"data: {_json.dumps(chunk)}\n\n"

        final_chunk = {
            "id": f"chatcmpl-{request_id}",
            "object": "chat.completion.chunk",
            "created": int(_t.time()),
            "model": model,
            "choices": [{"index": 0, "delta": {}, "finish_reason": resp.finish_reason or "stop"}],
        }
        yield f"data: {_json.dumps(final_chunk)}\n\n"
        yield "data: [DONE]\n\n"

    # ------------------------------------------------------------------
    # Core dispatch (separated for audit wrapping)
    # ------------------------------------------------------------------

    async def _dispatch(
        self,
        messages: list[dict[str, Any]],
        resolved_model: str,
        tools: list[ToolDefinition] | None,
        tool_choice: str | dict[str, Any],
        search_grounding: bool,
        max_tokens: int | None,
        temperature: float,
        stream: bool,
        _audit_evt: AuditEvent,
        _retry_counter_ref: list[int],
        _max_retries: int | None = None,
    ) -> ChatResponse:
        """Internal dispatch — routes to the correct API style."""

        force_legacy_backend = self._uses_litellm_backend() and resolved_model.startswith("legacy:")
        if force_legacy_backend:
            resolved_model = resolved_model.removeprefix("legacy:")

        # ── Thinking chain dispatch ──────────────────────────────────
        if resolved_model.startswith("gemma-think"):
            return await self._dispatch_thinking_chain(
                messages=messages,
                resolved_model=resolved_model,
                max_tokens=max_tokens,
            )

        preserve_native_grounding = self._should_preserve_native_grounding(
            resolved_model,
            search_grounding,
        )

        # ── LiteLLM backend dispatch ─────────────────────────────────
        # In migration mode, LiteLLM is the provider/routing layer.  The AIAT
        # client keeps response parsing, audit, metrics, and retry semantics.
        # Explicit Gemini search-grounding requests stay on AIAT's native
        # Gemini path because LiteLLM/OmniRoute do not expose this internal
        # feature contract without custom changes.
        if (
            self._uses_litellm_backend()
            and not force_legacy_backend
            and not preserve_native_grounding
        ):
            _audit_evt.resolved_model = resolved_model
            _audit_evt.provider = "litellm"
            _audit_evt.api_style = ApiStyle.CHAT_COMPLETIONS.value
            return await self._call_default_chat_completions_api(
                messages=messages,
                model=resolved_model,
                max_tokens=max_tokens,
                temperature=temperature,
                tools=tools,
                tool_choice=tool_choice,
                stream=stream,
                _retry_counter_ref=_retry_counter_ref,
                _max_retries=_max_retries,
            )

        # ── Pool resolution ──────────────────────────────────────────
        from .providers.api.openrouter import (
            OPENROUTER_FREE_ROUTER_MODEL_ID,
            OPENROUTER_FREE_ROUTER_WIRE_MODEL,
            ensure_free_openrouter_model,
        )

        if resolved_model == OPENROUTER_FREE_ROUTER_WIRE_MODEL:
            resolved_model = OPENROUTER_FREE_ROUTER_MODEL_ID

        entry, pool = self._registry.resolve_pool(resolved_model)
        if pool is not None and entry is None:
            raise LLMRateLimited(
                429,
                f"All models in pool '{resolved_model}' have reached their "
                f"rate-limit safety margin. Pool stats: {pool.stats()}",
            )

        concrete_model = entry.model_id if entry is not None else resolved_model
        wire_model = (
            entry.extra.get("api_model_name", concrete_model)
            if entry is not None
            else concrete_model
        )
        if entry is not None and entry.provider == "openrouter":
            try:
                wire_model = ensure_free_openrouter_model(wire_model)
            except ValueError as exc:
                raise LLMGatewayError(400, str(exc)) from exc
        elif entry is None and resolved_model.startswith("openrouter/"):
            raise LLMGatewayError(
                400,
                "Unknown raw OpenRouter model blocked: use a registered "
                "openrouter/...:free model or 'openrouter/free'.",
            )

        # Populate audit context
        _audit_evt.resolved_model = concrete_model
        if entry is not None:
            _audit_evt.provider = entry.provider
            _audit_evt.api_style = entry.api_style.value
        if pool is not None:
            _audit_evt.pool_id = pool.pool_id
            _audit_evt.pool_headroom = pool._headroom(
                concrete_model,
                _time.monotonic(),
            )
            logger.debug(
                "Pool '%s' picked model '%s' (headroom: %.2f)",
                pool.pool_id,
                concrete_model,
                _audit_evt.pool_headroom,
            )

        # Dispatch to the correct API style
        if entry is not None and entry.api_style == ApiStyle.RESPONSES:
            resp = await self._call_responses_api(
                entry=entry,
                messages=messages,
                model=wire_model,
                max_tokens=max_tokens,
                temperature=temperature,
            )
            if pool is not None:
                total_tokens = resp.usage.prompt_tokens + resp.usage.completion_tokens
                pool.record_request(concrete_model, total_tokens)
            return resp
        if entry is not None and entry.api_style == ApiStyle.CLI:
            resp = await self._call_cli(
                entry=entry,
                messages=messages,
                model=concrete_model,
            )
            if pool is not None:
                total_tokens = resp.usage.prompt_tokens + resp.usage.completion_tokens
                pool.record_request(concrete_model, total_tokens)
            return resp
        if (
            search_grounding
            and entry is not None
            and entry.provider == "gemini"
            and entry.capabilities.supports_search_grounding
        ):
            if tools:
                raise LLMGatewayError(
                    400,
                    "Gemini search-grounding mode does not yet combine with function tools.",
                )
            resp = await self._call_gemini_search_grounding_api(
                entry=entry,
                messages=messages,
                model=wire_model,
                max_tokens=max_tokens,
                temperature=temperature,
            )
            if pool is not None:
                total_tokens = resp.usage.prompt_tokens + resp.usage.completion_tokens
                pool.record_request(concrete_model, total_tokens)
            return resp

        # Default: chat_completions style
        client, endpoint = self._resolve_http_client_and_endpoint(entry)
        payload: dict[str, Any] = {
            "model": wire_model,
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
        max_retries = self._config.max_retries if _max_retries is None else _max_retries

        for attempt in range(max_retries + 1):
            try:
                if stream:
                    async with client.stream(
                        "POST",
                        endpoint,
                        json=payload,
                    ) as response:
                        if response.status_code == 200:
                            resp = await self._parse_stream_response(response)
                            if pool is not None:
                                total_tokens = (
                                    resp.usage.prompt_tokens + resp.usage.completion_tokens
                                )
                                pool.record_request(concrete_model, total_tokens)
                            return resp
                        detail = (await response.aread()).decode("utf-8", errors="replace")
                        if self._is_retryable_status(response.status_code):
                            _retry_counter_ref[0] = attempt + 1
                            if response.status_code == 429:
                                self.rate_limits.record_rate_limit(concrete_model)
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
            except httpx.TransportError as exc:
                _retry_counter_ref[0] = attempt + 1
                last_exc = exc
                if attempt < max_retries:
                    await asyncio.sleep(min(wait_s, self._config.retry_max_wait_s))
                    wait_s *= 2
                continue

            if response.status_code == 200:
                resp = self._parse_response(response.json())
                if pool is not None:
                    total_tokens = resp.usage.prompt_tokens + resp.usage.completion_tokens
                    pool.record_request(concrete_model, total_tokens)
                return resp

            if self._is_retryable_status(response.status_code):
                _retry_counter_ref[0] = attempt + 1
                if response.status_code == 429:
                    self.rate_limits.record_rate_limit(concrete_model)
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
    # Thinking chain
    # ------------------------------------------------------------------

    async def _dispatch_thinking_chain(
        self,
        messages: list[dict[str, Any]],
        resolved_model: str,
        max_tokens: int | None,
    ) -> ChatResponse:
        """Route ``gemma-think[/depth]`` to the multi-model reasoning pipeline.

        Accepted model strings:
        - ``"gemma-think"``          → standard depth
        - ``"gemma-think/light"``    → 2-stage (fast)
        - ``"gemma-think/standard"`` → 3-stage (default)
        - ``"gemma-think/deep"``     → 3-stage with self-critique
        """
        from .thinking import Depth, ThinkingChain

        parts = resolved_model.split("/", 1)
        depth_str = parts[1] if len(parts) > 1 else "standard"
        try:
            depth = Depth(depth_str)
        except ValueError:
            depth = Depth.STANDARD
            logger.warning(
                "Unknown thinking depth '%s', falling back to 'standard'",
                depth_str,
            )

        chain = ThinkingChain(self, depth=depth, max_tokens=max_tokens)
        result = await chain.think(messages, depth=depth, max_tokens=max_tokens)

        logger.info(
            "Thinking chain complete: depth=%s, stages=%d, total_tokens=%d, elapsed=%.1fs",
            depth.value,
            len(result.stages),
            result.response.usage.total_tokens,
            result.total_elapsed_s,
        )

        return result.response

    # ------------------------------------------------------------------
    # Audit / metrics recording helpers
    # ------------------------------------------------------------------

    def _record_success(
        self,
        audit_evt: AuditEvent,
        resp: ChatResponse,
        latency: float,
        retry_count: int = 0,
    ) -> None:
        """Record a successful LLM call in audit log, metrics, and rate tracker."""
        model_key = audit_evt.resolved_model or audit_evt.model

        # Audit event
        audit_evt.status = "success"
        audit_evt.status_code = 200
        audit_evt.latency_s = latency
        audit_evt.retry_count = retry_count
        audit_evt.finish_reason = resp.finish_reason
        audit_evt.prompt_tokens = resp.usage.prompt_tokens
        audit_evt.completion_tokens = resp.usage.completion_tokens
        audit_evt.total_tokens = resp.usage.total_tokens
        audit_evt.estimated_cost_usd = resp.usage.estimated_cost_usd
        audit_evt.tool_calls_returned = len(resp.tool_calls)
        if self.audit_log.level >= AuditLevel.FULL:
            audit_evt.response_text_length = len(resp.text)
        self.audit_log.record(audit_evt)

        # Metrics
        self.metrics.record_request(
            model=model_key,
            provider=audit_evt.provider,
            status="success",
            latency_s=latency,
            prompt_tokens=resp.usage.prompt_tokens,
            completion_tokens=resp.usage.completion_tokens,
            total_tokens=resp.usage.total_tokens,
            estimated_cost_usd=resp.usage.estimated_cost_usd,
            retry_count=retry_count,
        )

        # Rate-limit tracker (successful request)
        self.rate_limits.record_success(
            model=model_key,
            tokens=resp.usage.total_tokens,
            provider=audit_evt.provider or None,
        )

    def _record_failure(
        self,
        audit_evt: AuditEvent,
        status: str,
        status_code: int,
        latency: float,
        detail: str,
    ) -> None:
        """Record a failed LLM call in audit log, metrics, and rate tracker."""
        model_key = audit_evt.resolved_model or audit_evt.model

        audit_evt.status = status
        audit_evt.status_code = status_code
        audit_evt.latency_s = latency
        audit_evt.error_detail = detail[:500]  # truncate for safety
        self.audit_log.record(audit_evt)

        self.metrics.record_request(
            model=model_key,
            provider=audit_evt.provider,
            status=status,
            latency_s=latency,
        )

        if status == "rate_limited":
            self.rate_limits.record_rate_limit(model=model_key)

        # Only transient transport/provider failures arm cooldown state.
        # Permanent 4xx errors remain visible to operators without becoming a
        # hidden availability gate.
        if status == "timeout" or status_code in RETRYABLE_LLM_STATUS_CODES or status_code == 0:
            self.rate_limits.record_transient_failure(
                model=model_key,
                provider=audit_evt.provider or None,
                status_code=status_code,
                reason=detail,
            )

    def _record_stream_failure(
        self,
        model: str,
        exc: Exception,
        *,
        provider: str | None = None,
    ) -> None:
        """Record a final transient failure for the raw SSE path.

        ``stream_raw_sse`` intentionally bypasses ``chat_completion``'s
        audit wrapper, so it needs this small parity hook for cooldown state.
        """
        status_code = int(getattr(exc, "status_code", 0) or 0)
        retryable = isinstance(exc, httpx.TransportError) or (
            status_code in RETRYABLE_LLM_STATUS_CODES or status_code == 0
        )
        if retryable:
            self.rate_limits.record_transient_failure(
                model=model,
                provider=provider,
                status_code=status_code,
                reason=str(exc),
            )

    # ------------------------------------------------------------------
    # Observability accessors
    # ------------------------------------------------------------------

    @property
    def smart_router(self) -> Any:
        """Lazy-initialised ``SmartRouter`` backed by this client's metrics."""
        if self._smart_router is None:
            from .smart_router import SmartRouter

            self._smart_router = SmartRouter(
                metrics=self.metrics,
                rate_limits=self.rate_limits,
                provider_for_model=self._provider_id_for_model,
            )
        return self._smart_router

    def _provider_id_for_model(self, model: str) -> str | None:
        """Resolve a registered model's provider without consuming a pool."""
        entry = self._registry.get(model) if hasattr(self._registry, "get") else None
        return entry.provider if entry is not None else None

    def observability_dashboard(self) -> dict[str, Any]:
        """Return a combined dashboard: audit summary + metrics + rate limits + routing."""
        return {
            "audit": self.audit_log.summary(),
            "metrics": self.metrics.dashboard(),
            "rate_limits": self.rate_limits.dashboard(),
            "routing": self.smart_router.dashboard(),
        }

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

        # Normalise content — some providers return structured blocks (list of
        # {type, text/thinking} dicts for reasoning models) or non-string
        # scalars (e.g. Cloudflare returning bare int).  Flatten to a plain
        # string so downstream code sees a consistent type.
        raw_content = raw_message.get("content")
        if isinstance(raw_content, list):
            # Concatenate "text" fields from structured content blocks,
            # skipping thinking/tool blocks.
            parts: list[str] = []
            for block in raw_content:
                if isinstance(block, dict):
                    text = block.get("text") or block.get("content") or ""
                    if text:
                        parts.append(str(text))
                else:
                    parts.append(str(block))
            content: str | None = "".join(parts) if parts else None
        elif raw_content is not None:
            content = str(raw_content)
        else:
            content = None

        message = ChatMessage(
            role=raw_message.get("role", "assistant"),
            content=content,
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
        return status_code in RETRYABLE_LLM_STATUS_CODES

    @staticmethod
    def _log_retry(status_code: int, attempt: int, max_retries: int, wait_s: float) -> None:
        if attempt >= max_retries:
            logger.warning(
                "LLM gateway %d (attempt %d/%d), no retry budget remains",
                status_code,
                attempt + 1,
                max_retries + 1,
            )
            return
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

    def _uses_litellm_backend(self) -> bool:
        return self._config.backend.strip().lower() == "litellm"

    def _should_preserve_native_grounding(
        self,
        model: str,
        search_grounding: bool,
    ) -> bool:
        if not search_grounding:
            return False
        entry, _pool = self._registry.resolve_pool(model)
        return (
            entry is not None
            and entry.provider == "gemini"
            and entry.capabilities.supports_search_grounding
        )

    @staticmethod
    def _openai_safe_tool_name(name: str) -> str:
        """Return a provider-safe tool name for OpenAI-compatible gateways."""
        if _OPENAI_TOOL_NAME_RE.fullmatch(name):
            return name
        safe = re.sub(r"[^a-zA-Z0-9_-]", "__", name).strip("_")
        return safe or "tool"

    @classmethod
    def _serialise_openai_tools(
        cls,
        tools: list[ToolDefinition] | None,
    ) -> tuple[list[dict[str, Any]], dict[str, str]]:
        serialised: list[dict[str, Any]] = []
        name_map: dict[str, str] = {}
        if not tools:
            return serialised, name_map

        used: set[str] = set()
        for tool in tools:
            payload = tool.model_dump()
            original_name = tool.function.name
            base_safe_name = cls._openai_safe_tool_name(original_name)
            safe_name = base_safe_name
            if safe_name in used:
                suffix = 2
                candidate = f"{base_safe_name}_{suffix}"
                while candidate in used:
                    suffix += 1
                    candidate = f"{base_safe_name}_{suffix}"
                safe_name = candidate
            used.add(safe_name)
            payload["function"]["name"] = safe_name
            serialised.append(payload)
            if safe_name != original_name:
                name_map[safe_name] = original_name
        return serialised, name_map

    @staticmethod
    def _restore_tool_call_names(resp: ChatResponse, name_map: dict[str, str]) -> ChatResponse:
        if not name_map:
            return resp
        seen: set[int] = set()
        all_tool_calls = [*resp.tool_calls, *(resp.message.tool_calls or [])]
        for tool_call in all_tool_calls:
            tool_call_id = id(tool_call)
            if tool_call_id in seen:
                continue
            seen.add(tool_call_id)
            original_name = name_map.get(tool_call.function.name)
            if original_name:
                tool_call.function.name = original_name
        return resp

    async def _call_default_chat_completions_api(
        self,
        *,
        messages: list[dict[str, Any]],
        model: str,
        max_tokens: int | None,
        temperature: float,
        tools: list[ToolDefinition] | None,
        tool_choice: str | dict[str, Any],
        stream: bool,
        _retry_counter_ref: list[int],
        _max_retries: int | None,
    ) -> ChatResponse:
        """Call the configured OpenAI-compatible gateway directly.

        This is used by ``LLM_BACKEND=litellm`` so model aliases registered in
        LiteLLM are not intercepted by AIAT's legacy provider registry.
        """
        client = self._require_http()
        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
        }
        serialised_tools, tool_name_map = self._serialise_openai_tools(tools)
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens
        if serialised_tools:
            payload["tools"] = serialised_tools
            payload["tool_choice"] = tool_choice
        if stream:
            payload["stream"] = True
            payload["stream_options"] = {"include_usage": True}

        last_exc: Exception | None = None
        wait_s = self._config.retry_min_wait_s
        max_retries = self._config.max_retries if _max_retries is None else _max_retries

        for attempt in range(max_retries + 1):
            try:
                if stream:
                    async with client.stream(
                        "POST",
                        "/v1/chat/completions",
                        json=payload,
                    ) as response:
                        if response.status_code == 200:
                            return self._restore_tool_call_names(
                                await self._parse_stream_response(response),
                                tool_name_map,
                            )
                        detail = (await response.aread()).decode("utf-8", errors="replace")
                        if self._is_retryable_status(response.status_code):
                            _retry_counter_ref[0] = attempt + 1
                            if response.status_code == 429:
                                self.rate_limits.record_rate_limit(model)
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
            except httpx.TransportError as exc:
                _retry_counter_ref[0] = attempt + 1
                last_exc = exc
                if attempt < max_retries:
                    await asyncio.sleep(min(wait_s, self._config.retry_max_wait_s))
                    wait_s *= 2
                continue

            if response.status_code == 200:
                return self._restore_tool_call_names(
                    self._parse_response(response.json()),
                    tool_name_map,
                )

            if self._is_retryable_status(response.status_code):
                _retry_counter_ref[0] = attempt + 1
                if response.status_code == 429:
                    self.rate_limits.record_rate_limit(model)
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

    async def _stream_default_raw_sse(
        self,
        *,
        messages: list[dict[str, Any]],
        model: str,
        temperature: float,
        max_tokens: int | None,
        tools: list[ToolDefinition] | None,
        tool_choice: str | dict[str, Any],
    ) -> AsyncIterator[str]:
        """Stream raw SSE from the configured OpenAI-compatible gateway."""
        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        serialised_tools, _tool_name_map = self._serialise_openai_tools(tools)
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens
        if serialised_tools:
            payload["tools"] = serialised_tools
            payload["tool_choice"] = tool_choice

        http_client = self._require_http()
        wait_s = self._config.retry_min_wait_s
        max_retries = self._config.max_retries
        last_exc: Exception | None = None

        for attempt in range(max_retries + 1):
            try:
                async with http_client.stream(
                    "POST",
                    "/v1/chat/completions",
                    json=payload,
                ) as response:
                    if response.status_code == 200:
                        async for raw_line in response.aiter_lines():
                            if raw_line:
                                yield raw_line + "\n\n"
                        return
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
                    raise LLMGatewayError(response.status_code, detail)
            except httpx.TransportError as exc:
                last_exc = exc
                if attempt < max_retries:
                    await asyncio.sleep(min(wait_s, self._config.retry_max_wait_s))
                    wait_s *= 2
                continue

        if last_exc is not None:
            self._record_stream_failure(model, last_exc, provider="litellm")
        raise last_exc or LLMGatewayError(0, "Unknown error after retries exhausted")

    # ------------------------------------------------------------------
    # Provider-aware HTTP client + endpoint resolution
    # ------------------------------------------------------------------

    def _resolve_http_client_and_endpoint(
        self,
        entry: ModelEntry | None,
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

    @staticmethod
    def _task_requests_search_grounding(task: str | None) -> bool:
        """Return True when the task hint clearly asks for grounding."""
        return task in {"search-grounding", "grounding", "web-search", "url-context"}

    def _resolve_gemini_native_client_and_endpoint(
        self,
        entry: ModelEntry,
    ) -> tuple[httpx.AsyncClient, str]:
        """Return a Gemini-native client and ``generateContent`` endpoint."""
        provider = self._registry.get_provider(entry.provider)
        if provider is None:
            return self._require_http(), f"/v1beta/models/{entry.model_id}:generateContent"

        pid = f"{provider.provider_id}:native"
        if pid not in self._provider_clients:
            api_key = provider.resolve_api_key()
            headers: dict[str, str] = {
                "Content-Type": "application/json",
                "x-goog-api-key": api_key,
                **provider.extra_headers,
            }
            self._provider_clients[pid] = httpx.AsyncClient(
                headers=headers,
                timeout=self._config.timeout_s,
            )

        base_url = (
            provider.base_url[:-7] if provider.base_url.endswith("/openai") else provider.base_url
        )
        endpoint = f"{base_url}/models/{entry.model_id}:generateContent"
        return self._provider_clients[pid], endpoint

    @staticmethod
    def _gemini_text_from_content(content: Any) -> str:
        """Extract text content for Gemini-native requests.

        Search grounding is currently implemented as a text-only path.
        Images are rejected instead of being silently dropped.
        """
        text, image_urls = LLMGatewayClient._extract_text_and_images(content)
        if image_urls:
            raise LLMGatewayError(
                400,
                "Gemini search-grounding path currently supports text-only messages.",
            )
        return text

    @classmethod
    def _messages_to_gemini_contents(
        cls,
        messages: list[dict[str, Any]],
    ) -> tuple[str, list[dict[str, Any]]]:
        """Convert OpenAI-style messages into Gemini contents + system instruction."""
        system_parts: list[str] = []
        contents: list[dict[str, Any]] = []

        for msg in messages:
            role = msg.get("role", "user")
            text = cls._gemini_text_from_content(msg.get("content", ""))
            if not text and role != "system":
                continue

            if role == "system":
                if text:
                    system_parts.append(text)
                continue

            gemini_role = "model" if role == "assistant" else "user"

            if role == "tool":
                tool_name = msg.get("name") or "tool"
                text = f"[Tool result: {tool_name}]\n{text}".strip()
                gemini_role = "user"

            contents.append(
                {
                    "role": gemini_role,
                    "parts": [{"text": text}],
                }
            )

        system_instruction = "\n\n".join(system_parts).strip()
        return system_instruction, contents

    @staticmethod
    def _parse_gemini_native_response(
        data: dict[str, Any],
        *,
        model: str = "",
    ) -> ChatResponse:
        """Parse Gemini REST ``generateContent`` output into ``ChatResponse``."""
        candidates = data.get("candidates") or []
        candidate = candidates[0] if candidates else {}
        content = candidate.get("content") or {}
        parts = content.get("parts") if isinstance(content, dict) else []

        text_parts: list[str] = []
        if isinstance(parts, list):
            for part in parts:
                if not isinstance(part, dict):
                    continue
                if part.get("thought"):
                    continue
                text = part.get("text")
                if isinstance(text, str) and text:
                    text_parts.append(text)

        raw_usage = data.get("usageMetadata", {}) or {}
        usage = UsageStats(
            prompt_tokens=int(raw_usage.get("promptTokenCount", 0) or 0),
            completion_tokens=int(raw_usage.get("candidatesTokenCount", 0) or 0),
            total_tokens=int(
                raw_usage.get(
                    "totalTokenCount",
                    int(raw_usage.get("promptTokenCount", 0) or 0)
                    + int(raw_usage.get("candidatesTokenCount", 0) or 0),
                )
                or 0
            ),
        )
        if usage.total_tokens == 0:
            usage.total_tokens = usage.prompt_tokens + usage.completion_tokens

        finish_reason = str(candidate.get("finishReason", "stop")).lower()
        extra = {
            "response_id": data.get("responseId", ""),
            "model_version": data.get("modelVersion", ""),
            "grounding_metadata": candidate.get("groundingMetadata"),
            "usage_metadata": raw_usage,
        }

        return ChatResponse(
            response_id=data.get("responseId", ""),
            model=model,
            finish_reason=finish_reason,
            message=ChatMessage(role="assistant", content="".join(text_parts).strip() or None),
            usage=usage,
            extra=extra,
        )

    async def _call_gemini_search_grounding_api(
        self,
        *,
        entry: ModelEntry,
        messages: list[dict[str, Any]],
        model: str,
        max_tokens: int | None = None,
        temperature: float = 0.7,
    ) -> ChatResponse:
        """Call Gemini's native ``generateContent`` endpoint with Google Search."""
        client, endpoint = self._resolve_gemini_native_client_and_endpoint(entry)
        system_instruction, contents = self._messages_to_gemini_contents(messages)
        if not contents:
            raise LLMGatewayError(
                400,
                "Gemini search-grounding path requires at least one non-system message.",
            )

        payload: dict[str, Any] = {
            "contents": contents,
            "tools": [{"google_search": {}}],
            "generationConfig": {"temperature": temperature},
        }
        if system_instruction:
            payload["systemInstruction"] = {
                "parts": [{"text": system_instruction}],
            }
        if max_tokens is not None:
            payload["generationConfig"]["maxOutputTokens"] = max_tokens

        last_exc: Exception | None = None
        wait_s = self._config.retry_min_wait_s
        max_retries = self._config.max_retries

        for attempt in range(max_retries + 1):
            try:
                response = await client.post(endpoint, json=payload)
            except httpx.TransportError as exc:
                last_exc = exc
                if attempt < max_retries:
                    await asyncio.sleep(min(wait_s, self._config.retry_max_wait_s))
                    wait_s *= 2
                continue

            if response.status_code == 200:
                return self._parse_gemini_native_response(response.json(), model=model)

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

        # Build responses-API payload.  Content can be a plain string *or*
        # an OpenAI-style content array with text/image_url parts.  We
        # normalise both into the Responses-format input blocks.
        input_blocks: list[dict[str, Any]] = []
        for msg in messages:
            role = msg.get("role", "user")
            raw_content = msg.get("content", "")
            content = self._normalise_content_for_responses(raw_content)
            input_blocks.append({"role": role, "content": content})

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
            except httpx.TransportError as exc:
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
    def _normalise_content_for_responses(
        content: Any,
    ) -> Any:
        """Convert an OpenAI content array into Responses-API input blocks.

        If *content* is a plain string it is returned as-is (Zen accepts
        plain strings).  If it is a list of ``{type: text/image_url}``
        parts, each part is converted:

        * ``{"type": "text", "text": "..."}``
          → ``{"type": "input_text", "text": "..."``}
        * ``{"type": "image_url", "image_url": {"url": "..."}``}
          → ``{"type": "input_image", "image_url": "..."}``
        """
        if isinstance(content, str):
            return content or ""
        if not isinstance(content, list):
            return str(content) if content else ""

        parts: list[dict[str, Any]] = []
        for item in content:
            if not isinstance(item, dict):
                continue
            ctype = item.get("type", "")
            if ctype == "text":
                parts.append({"type": "input_text", "text": item.get("text", "")})
            elif ctype == "image_url":
                url_data = item.get("image_url", {})
                url = url_data.get("url", "") if isinstance(url_data, dict) else str(url_data)
                if url:
                    parts.append({"type": "input_image", "image_url": url})
        return parts if parts else ""

    @staticmethod
    def _extract_text_and_images(
        content: Any,
    ) -> tuple[str, list[str]]:
        """Split a content value into text and a list of image URLs.

        Works with both plain string content and OpenAI content arrays
        ``[{"type": "text", ...}, {"type": "image_url", ...}]``.

        Returns ``(text, image_urls)`` where *image_urls* may contain
        base64 data-URLs or HTTPS URLs.
        """
        if isinstance(content, str):
            return content, []
        if not isinstance(content, list):
            return (str(content) if content else ""), []

        text_parts: list[str] = []
        image_urls: list[str] = []
        for item in content:
            if not isinstance(item, dict):
                continue
            ctype = item.get("type", "")
            if ctype == "text":
                text_parts.append(item.get("text", ""))
            elif ctype == "image_url":
                url_data = item.get("image_url", {})
                url = url_data.get("url", "") if isinstance(url_data, dict) else str(url_data)
                if url:
                    image_urls.append(url)
        return " ".join(text_parts), image_urls

    @staticmethod
    def _parse_responses_api(
        data: dict[str, Any],
        *,
        model: str = "",
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
            "completion_tokens": raw_usage.get(
                "output_tokens", raw_usage.get("completion_tokens", 0)
            ),
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
        # Content may be a plain string or an OpenAI content array with
        # text + image_url parts.  We extract text for the prompt and
        # collect image data-URLs for attachment via the -a flag.
        prompt_parts: list[str] = []
        image_urls: list[str] = []  # base64 data-URLs or https URLs
        for msg in messages:
            role = msg.get("role", "user")
            raw_content = msg.get("content", "")
            text, imgs = self._extract_text_and_images(raw_content)
            image_urls.extend(imgs)
            prefix = {"system": "[System]", "user": "[User]", "assistant": "[Assistant]"}
            prompt_parts.append(f"{prefix.get(role, '[User]')} {text}")
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

        # Note: image_urls are extracted but CLI providers (e.g. Copilot CLI)
        # do not currently support image attachment flags.  However, they
        # *can* read files from disk when granted via --add-dir.  We save
        # images to a temp directory and inject file references into the
        # prompt so the CLI model can access them.
        attachment_mgr = None
        if image_urls:
            from ..agent_runtime.attachment_manager import TempAttachmentManager

            attachment_mgr = TempAttachmentManager()
            attachment_mgr.setup()
            saved = attachment_mgr.process_image_urls_for_cli(image_urls)
            if saved:
                # Add --add-dir <temp_dir> so the CLI model can read the files
                cmd.extend(attachment_mgr.get_cli_args())
                # Append file reference text to the prompt
                file_refs = attachment_mgr.build_cli_file_references()
                if use_stdin:
                    prompt = prompt + "\n\n" + file_refs
                else:
                    # Re-build the flag-mode prompt with file references
                    # Remove the old -p <prompt> pair and re-add with updated prompt
                    flag = entry.cli_prompt_flag
                    if flag and flag in cmd:
                        idx = cmd.index(flag)
                        cmd[idx + 1] = cmd[idx + 1] + "\n\n" + file_refs
                logger.info(
                    "CLI model %s: %d image(s) saved to %s for --add-dir access",
                    model,
                    len(saved),
                    attachment_mgr.temp_dir,
                )
            else:
                logger.warning(
                    "CLI model %s: %d image(s) could not be saved (not data-URLs?)",
                    model,
                    len(image_urls),
                )

        logger.info("CLI model %s: running %s (stdin=%s)", model, cmd[0], use_stdin)

        try:
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
        finally:
            # Clean up temp attachment directory (if any)
            if attachment_mgr is not None:
                attachment_mgr.cleanup()

    # ------------------------------------------------------------------
    # Convenience — agent-friendly high-level API
    # ------------------------------------------------------------------

    async def ask(
        self,
        prompt: str,
        *,
        system: str | None = None,
        model: str | None = None,
        task: str | None = None,
        tools: list[ToolDefinition] | None = None,
        tool_choice: str | dict[str, Any] = "auto",
        search_grounding: bool = False,
        max_tokens: int | None = None,
        temperature: float = 0.7,
        use_fallback: bool = True,
    ) -> str:
        """One-liner for agents — send a prompt, get back a text string.

        This is the simplest way to call an LLM from an agent.  No need
        to build a message list manually.

        Parameters
        ----------
        prompt:
            The user's question or instruction.
        system:
            Optional system/developer prompt.  When omitted no system
            message is added.
        model:
            Model to use.  When ``None``, the best available free model
            is selected automatically via ``ModelSelector``.
        task:
            Optional task hint used by ``ModelSelector`` when ``model``
            is ``None`` (e.g. ``"reasoning"``, ``"code-generation"``).
        tools:
            Tool definitions to pass to the LLM.
        tool_choice:
            Tool choice strategy.
        search_grounding:
            Enable Gemini search grounding when the selected model supports it.
        max_tokens:
            Hard cap on completion tokens.
        temperature:
            Sampling temperature.
        use_fallback:
            When ``True``, automatically retries with the next-best model
            if the first choice fails.

        Returns
        -------
        str
            The assistant's text response (empty string if the model
            returned only tool calls).
        """
        messages: list[dict[str, Any]] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        effective_search_grounding = search_grounding or self._task_requests_search_grounding(task)

        resolved_model = model or self._auto_select_model(
            task=task,
            needs_tools=bool(tools),
            needs_search_grounding=effective_search_grounding,
        )

        if use_fallback and model is None:
            resp = await self.chat_completion_with_fallback(
                messages=messages,
                task=task,
                tools=tools,
                tool_choice=tool_choice,
                search_grounding=effective_search_grounding,
                max_tokens=max_tokens,
                temperature=temperature,
                needs_tools=bool(tools),
                needs_search_grounding=effective_search_grounding,
            )
        else:
            resp = await self.chat_completion(
                messages=messages,
                model=resolved_model,
                tools=tools,
                tool_choice=tool_choice,
                search_grounding=effective_search_grounding,
                max_tokens=max_tokens,
                temperature=temperature,
            )
        return resp.text

    def chat(
        self,
        *,
        system: str | None = None,
        model: str | None = None,
        task: str | None = None,
        search_grounding: bool = False,
        temperature: float = 0.7,
        max_tokens: int | None = None,
    ) -> _ConversationContext:
        """Return a stateful multi-turn conversation context manager.

        Usage::

            async with client.chat(system="You are a helpful assistant.") as conv:
                r1 = await conv.send("What is 2 + 2?")
                r2 = await conv.send("And what is that squared?")
                history = conv.history   # full message list

        Parameters
        ----------
        system:
            Optional system prompt injected as the first message.
        model:
            Model to use.  ``None`` = auto-select.
        task:
            Optional task hint for model auto-selection.
        search_grounding:
            Enable Gemini search grounding for supported models.
        temperature:
            Sampling temperature for all turns in this conversation.
        max_tokens:
            Token cap for each turn.

        Returns
        -------
        _ConversationContext
            An async context manager.  Call ``await conv.send(text)``
            inside the block to send messages and receive responses.
        """
        return _ConversationContext(
            client=self,
            system=system,
            model=model,
            task=task,
            search_grounding=search_grounding or self._task_requests_search_grounding(task),
            temperature=temperature,
            max_tokens=max_tokens,
        )

    async def chat_completion_with_fallback(
        self,
        messages: list[dict[str, Any]],
        *,
        task: str | None = None,
        model: str | None = None,
        tools: list[ToolDefinition] | None = None,
        tool_choice: str | dict[str, Any] = "auto",
        search_grounding: bool = False,
        max_tokens: int | None = None,
        temperature: float = 0.7,
        stream: bool = False,
        needs_tools: bool = False,
        needs_vision: bool = False,
        needs_search_grounding: bool = False,
        chain_length: int = 4,
    ) -> ChatResponse:
        """Call an LLM with automatic model fallback on failure.

        Builds a ranked fallback chain via ``ModelSelector`` and tries
        each candidate in order until one succeeds.  This makes agent
        code resilient to individual provider outages, rate limits, and
        transient errors without any extra boilerplate.

        Parameters
        ----------
        messages:
            Conversation history in OpenAI format.
        task:
            Optional task hint for model ranking
            (e.g. ``"reasoning"``, ``"code-generation"``).
        model:
            If provided, this model is tried first before the fallback
            chain.
        tools:
            Tool definitions to pass to the LLM.
        tool_choice:
            Tool choice strategy.
        search_grounding:
            Enable Gemini search grounding when supported by the selected model.
        max_tokens:
            Token cap.
        temperature:
            Sampling temperature.
        stream:
            Enable SSE streaming.
        needs_tools:
            Require tool-calling support in the fallback chain.
        needs_vision:
            Require vision support in the fallback chain.
        needs_search_grounding:
            Require built-in search grounding support in the fallback chain.
        chain_length:
            Maximum number of models to try before giving up.

        Returns
        -------
        ChatResponse
            Response from the first successful model.

        Raises
        ------
        LLMGatewayError
            When all models in the chain fail.
        """
        selector = self.model_selector

        # Build the fallback chain
        chain: list[str] = []
        if model:
            chain.append(model)
        elif self._uses_litellm_backend() and not (needs_search_grounding or search_grounding):
            chain.append(self._config.default_model)

        ranked = selector.fallback_chain(
            task=task,
            needs_tools=needs_tools or bool(tools),
            needs_vision=needs_vision,
            needs_search_grounding=needs_search_grounding or search_grounding,
            chain_length=chain_length,
        )
        for m in ranked:
            if m not in chain:
                chain.append(m)
                if len(chain) >= chain_length:
                    break

        # Do not repeatedly send automatic fallback traffic to endpoints that
        # are already cooling down. If every candidate is cooling, probe the
        # one that expires first so an outage can recover without a hard gate.
        available_chain = self.rate_limits.available_models(
            chain,
            provider_for_model=self._provider_id_for_model,
        )
        probe_override: str | None = None
        if available_chain:
            skipped = [candidate for candidate in chain if candidate not in available_chain]
            if skipped:
                logger.info("Fallback skipped cooling endpoints: %s", skipped)
            chain = available_chain
        elif chain:
            probe = self.rate_limits.earliest_available_model(
                chain,
                provider_for_model=self._provider_id_for_model,
            )
            chain = [probe] if probe else chain
            probe_override = probe
            logger.warning("Fallback chain is cooling; probing earliest endpoint '%s'", chain[0])

        last_exc: Exception | None = None
        tried: list[str] = []
        for candidate in chain:
            # A prior candidate may have armed a provider-wide cooldown during
            # this same request. Re-check immediately before each attempt.
            if candidate != probe_override and self.rate_limits.is_in_cooldown(
                candidate,
                provider=self._provider_id_for_model(candidate),
            ):
                logger.info("Fallback skipped endpoint '%s' after a new cooldown", candidate)
                continue
            try:
                resp = await self.chat_completion(
                    messages=messages,
                    model=candidate,
                    tools=tools,
                    tool_choice=tool_choice,
                    search_grounding=search_grounding,
                    max_tokens=max_tokens,
                    temperature=temperature,
                    stream=stream,
                    max_retries=0,
                )
                if tried:
                    logger.info(
                        "Fallback succeeded on '%s' after %d failed attempt(s): %s",
                        candidate,
                        len(tried),
                        tried,
                    )
                return resp
            except (LLMRateLimited, LLMGatewayError, httpx.TransportError) as exc:
                logger.warning(
                    "Fallback: model '%s' failed (%s), trying next in chain",
                    candidate,
                    type(exc).__name__,
                )
                tried.append(candidate)
                last_exc = exc

        raise last_exc or LLMGatewayError(
            0,
            f"All models in fallback chain failed: {chain}",
        )

    def auto_select_model(
        self,
        *,
        task: str | None = None,
        needs_tools: bool = False,
        needs_vision: bool = False,
        needs_reasoning: bool = False,
        needs_search_grounding: bool = False,
        min_context: int = 0,
        exclude: list[str] | None = None,
        fallback: str | None = None,
    ) -> str:
        """Pick the best available model using ``ModelSelector``.

        This is a convenience wrapper around ``ModelSelector.pick()``
        exposed directly on the client so agents don't need to
        instantiate the selector manually.

        Parameters
        ----------
        task:
            Optional task hint.
        needs_tools:
            Require tool-calling support.
        needs_vision:
            Require vision support.
        needs_reasoning:
            Require explicit reasoning capability.
        needs_search_grounding:
            Require built-in search grounding support.
        min_context:
            Minimum context window size in tokens.
        exclude:
            Model IDs to skip.
        fallback:
            Fallback model ID if nothing qualifies.

        Returns
        -------
        str
            The best model ID.
        """
        return self.model_selector.pick(
            task=task,
            needs_tools=needs_tools,
            needs_vision=needs_vision,
            needs_reasoning=needs_reasoning,
            needs_search_grounding=needs_search_grounding,
            min_context=min_context,
            exclude=exclude,
            fallback=fallback,
        )

    # ------------------------------------------------------------------
    # Observability accessors
    # ------------------------------------------------------------------

    @property
    def model_selector(self) -> Any:
        """Lazy-initialised ``ModelSelector`` backed by this client."""
        if self._model_selector is None:
            from .model_selector import ModelSelector

            self._model_selector = ModelSelector(self)
        return self._model_selector

    # ------------------------------------------------------------------
    # Registry
    # ------------------------------------------------------------------

    def list_models(self, provider: str | None = None) -> list[ModelEntry]:
        """Return registered models from the registry."""
        return self._registry.list_models(provider)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _auto_select_model(
        self,
        *,
        task: str | None = None,
        needs_tools: bool = False,
        needs_vision: bool = False,
        needs_search_grounding: bool = False,
    ) -> str:
        """Return the best model ID (internal, no exclude support)."""
        if self._uses_litellm_backend() and not needs_search_grounding:
            return self._config.default_model
        return self.model_selector.pick(
            task=task,
            needs_tools=needs_tools,
            needs_vision=needs_vision,
            needs_search_grounding=needs_search_grounding,
        )

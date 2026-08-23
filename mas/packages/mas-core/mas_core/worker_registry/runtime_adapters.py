"""Certified runtime adapters for external worker transports.

The adapters in this module only translate transport behavior into universal
worker events. They never update worker, flow, project, or approval state.
Runtime configuration is treated as untrusted input and commands are executed
without a shell.
"""

from __future__ import annotations

import asyncio
import hashlib
import importlib
import json
import logging
import math
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

import httpx

from mas_core.worker_contract import (
    AdapterContext,
    BaseWorkerAdapter,
    CancellationMode,
    CheckpointMode,
    EventType,
    MemoryMode,
    ModelMode,
    NativeWorkerAdapter,
    StreamingMode,
    ToolMode,
    WorkerCancellation,
    WorkerCapabilities,
    WorkerError,
    WorkerEvent,
    WorkerHealth,
    WorkerReadiness,
    WorkerResult,
    WorkerRunRequest,
    WorkerToolRequest,
    WorkerToolResponse,
    WorkerUsage,
    issue_opencode_tool_grant,
)
from mas_core.worker_registry.firecracker import FirecrackerLaunchSpec

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable
    from uuid import UUID


def _sanitize_openapi_value(value: Any, key: str = "") -> Any:
    if re.search(r"(?:password|secret|token|authorization|cookie|api[_-]?key|private[_-]?key)", key, re.I):
        return "[REDACTED]"
    if isinstance(value, dict):
        return {str(k): _sanitize_openapi_value(v, str(k)) for k, v in value.items()}
    if isinstance(value, list):
        return [_sanitize_openapi_value(item, key) for item in value]
    return value


def _external_capabilities(
    *,
    checkpoint_mode: CheckpointMode = CheckpointMode.UNSUPPORTED,
    cancellation_mode: CancellationMode = CancellationMode.COOPERATIVE,
    streaming_mode: StreamingMode = StreamingMode.EVENT_STREAM,
    memory_mode: MemoryMode = MemoryMode.AIAT,
    workspace_mode: str = "isolated",
    model_mode: ModelMode = ModelMode.AIAT_GATEWAY,
    capability_names: list[str] | None = None,
) -> WorkerCapabilities:
    return WorkerCapabilities(
        checkpoint_mode=checkpoint_mode,
        cancellation_mode=cancellation_mode,
        streaming_mode=streaming_mode,
        tool_mode=ToolMode.AIAT_MEDIATED,
        memory_mode=memory_mode,
        workspace_mode=workspace_mode,
        model_mode=model_mode,
        capability_names=capability_names or [],
    )


class ProcessAdapter(BaseWorkerAdapter):
    """Adapter for a certified process/stdio worker.

    The command is a list of arguments and is never passed through a shell.
    A runtime must emit JSON lines for normalized events or a final JSON result;
    non-JSON stdout is retained as output text and does not become authority.
    """

    runtime_type = "process"

    def __init__(
        self,
        command: list[str],
        *,
        worker_id: str,
        cwd: str | None = None,
        environment: dict[str, str] | None = None,
        capabilities: WorkerCapabilities | None = None,
        context: AdapterContext | None = None,
        runner: Callable[..., Awaitable[Any]] | None = None,
        runtime_version: str | None = None,
    ) -> None:
        if not command or any(not isinstance(part, str) or not part for part in command):
            raise ValueError("process adapter command must be a non-empty argument list")
        super().__init__(
            worker_id=worker_id,
            capabilities=capabilities or _external_capabilities(),
            context=context,
            runtime_version=runtime_version,
        )
        self.command = list(command)
        self.cwd = cwd
        self.environment = dict(environment or {})
        self._runner = runner
        self._processes: dict[UUID, asyncio.subprocess.Process] = {}

    async def _execute(self, request: WorkerRunRequest) -> Any:
        if self._runner is not None:
            return await self._runner(request, self)
        process = await asyncio.create_subprocess_exec(
            *self.command,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=self.cwd,
            env=self.environment or None,
        )
        self._processes[request.run_id] = process
        payload = json.dumps(request.model_dump(mode="json"), separators=(",", ":")) + "\n"
        assert process.stdin is not None
        process.stdin.write(payload.encode())
        await process.stdin.drain()
        process.stdin.close()
        outputs: list[str] = []
        final: WorkerResult | None = None
        assert process.stdout is not None
        assert process.stderr is not None
        # Drain stderr while stdout is being consumed. A verbose but valid
        # worker must not deadlock once the OS stderr pipe buffer fills.
        stderr_task = asyncio.create_task(process.stderr.read())
        try:
            async for raw_line in process.stdout:
                line = raw_line.decode(errors="replace").rstrip("\n")
                if not line:
                    continue
                try:
                    data = json.loads(line)
                except json.JSONDecodeError:
                    outputs.append(line)
                    continue
                if isinstance(data, dict) and "event_type" in data:
                    event = WorkerEvent.model_validate({
                        "run_id": request.run_id,
                        "worker_id": self.worker_id,
                        **data,
                    })
                    await self._emit(event)
                    if event.result is not None:
                        final = event.result
                elif isinstance(data, dict) and "success" in data:
                    final = WorkerResult.model_validate({
                        "run_id": request.run_id,
                        "worker_id": self.worker_id,
                        **data,
                    })
                else:
                    outputs.append(json.dumps(data, sort_keys=True))
            await process.wait()
            stderr = (await stderr_task).decode(errors="replace")[-4000:]
            if final is not None:
                return final
            if process.returncode:
                return WorkerResult(
                    run_id=request.run_id,
                    worker_id=self.worker_id,
                    success=False,
                    error=WorkerError(
                        code="PROCESS_EXIT",
                        message=stderr or f"process exited with code {process.returncode}",
                        retryable=True,
                        category="transport",
                        details={"returncode": process.returncode},
                    ),
                )
            return {"success": True, "output": "\n".join(outputs)}
        finally:
            if not stderr_task.done():
                stderr_task.cancel()
                await asyncio.gather(stderr_task, return_exceptions=True)
            self._processes.pop(request.run_id, None)

    async def cancel(self, request: WorkerCancellation) -> None:
        process = self._processes.get(request.run_id)
        if process is not None:
            if request.force:
                process.kill()
            else:
                process.terminate()
        await super().cancel(request)


class HTTPAdapter(BaseWorkerAdapter):
    """Adapter for a certified HTTP worker service."""

    runtime_type = "http"

    def __init__(
        self,
        base_url: str,
        *,
        worker_id: str,
        endpoints: dict[str, str] | None = None,
        headers: dict[str, str] | None = None,
        capabilities: WorkerCapabilities | None = None,
        context: AdapterContext | None = None,
        client: httpx.AsyncClient | None = None,
        timeout_seconds: float = 60.0,
        runtime_version: str | None = None,
    ) -> None:
        super().__init__(
            worker_id=worker_id,
            capabilities=capabilities or _external_capabilities(),
            context=context,
            runtime_version=runtime_version,
        )
        self.base_url = base_url.rstrip("/")
        self.endpoints = {
            "health": "/health",
            "readiness": "/readiness",
            "run": "/runs",
            "cancel": "/runs/{run_id}/cancel",
            **(endpoints or {}),
        }
        self.headers = dict(headers or {})
        self._client = client
        self.timeout_seconds = timeout_seconds
        self._owned_client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is not None:
            return self._client
        if self._owned_client is None:
            self._owned_client = httpx.AsyncClient(
                base_url=self.base_url,
                headers=self.headers,
                timeout=self.timeout_seconds,
            )
        return self._owned_client

    async def health(self) -> WorkerHealth:
        try:
            client = await self._get_client()
            response = await client.get(self.endpoints["health"])
            response.raise_for_status()
            data = response.json() if response.content else {}
            return WorkerHealth(
                worker_id=self.worker_id,
                healthy=bool(data.get("healthy", True)),
                status=str(data.get("status", "healthy")),
                runtime_version=data.get("version") or self.runtime_version,
                adapter_version=self.adapter_api_version,
                details=data if isinstance(data, dict) else {},
            )
        except Exception as exc:
            return WorkerHealth(
                worker_id=self.worker_id,
                healthy=False,
                status="unreachable",
                runtime_version=self.runtime_version,
                adapter_version=self.adapter_api_version,
                details={"error": str(exc)},
            )

    async def readiness(self, request: WorkerRunRequest | None = None) -> WorkerReadiness:
        local = await super().readiness(request)
        if not local.ready:
            return local
        try:
            client = await self._get_client()
            response = await client.get(self.endpoints["readiness"])
            response.raise_for_status()
            data = response.json() if response.content else {}
            remote_ready = bool(data.get("ready", data.get("status") in {None, "ready", "healthy"}))
            blockers = list(data.get("blockers") or []) if isinstance(data, dict) else []
            return WorkerReadiness(
                worker_id=self.worker_id,
                ready=remote_ready and not blockers,
                checks={"adapter_open": True, "runtime_ready": remote_ready},
                blockers=[str(blocker) for blocker in blockers],
            )
        except Exception as exc:
            return WorkerReadiness(
                worker_id=self.worker_id,
                ready=False,
                checks={"adapter_open": True, "runtime_ready": False},
                blockers=[f"runtime readiness failed: {exc}"],
            )

    async def _execute(self, request: WorkerRunRequest) -> Any:
        client = await self._get_client()
        response = await client.post(self.endpoints["run"], json=request.model_dump(mode="json"))
        response.raise_for_status()
        data = response.json() if response.content else {}
        if isinstance(data, dict) and isinstance(data.get("events"), list):
            final: WorkerResult | None = None
            for raw in data["events"]:
                event = WorkerEvent.model_validate({
                    "run_id": request.run_id,
                    "worker_id": self.worker_id,
                    **raw,
                })
                await self._emit(event)
                final = event.result or final
            if final is not None:
                return final
        if isinstance(data, dict) and "success" in data:
            return WorkerResult.model_validate({
                "run_id": request.run_id,
                "worker_id": self.worker_id,
                **data,
            })
        return {"success": True, "output": data}

    async def cancel(self, request: WorkerCancellation) -> None:
        client = await self._get_client()
        endpoint = self.endpoints["cancel"].format(run_id=request.run_id)
        try:
            response = await client.post(endpoint, json=request.model_dump(mode="json"))
            response.raise_for_status()
        finally:
            await super().cancel(request)

    async def close(self) -> None:
        await super().close()
        if self._owned_client is not None:
            await self._owned_client.aclose()
            self._owned_client = None


class MCPClient(Protocol):
    async def health(self) -> dict[str, Any]: ...
    async def run(self, request: dict[str, Any]) -> Any: ...
    async def cancel(self, request: dict[str, Any]) -> Any: ...


class MCPAdapter(BaseWorkerAdapter):
    """Adapter for a certified MCP server client supplied by tool-service."""

    runtime_type = "mcp"

    def __init__(
        self,
        client: MCPClient,
        *,
        worker_id: str,
        capabilities: WorkerCapabilities | None = None,
        context: AdapterContext | None = None,
        runtime_version: str | None = None,
    ) -> None:
        super().__init__(
            worker_id=worker_id,
            capabilities=capabilities or _external_capabilities(
                checkpoint_mode=CheckpointMode.RESTART_ONLY,
                cancellation_mode=CancellationMode.AFTER_CURRENT_STEP,
                streaming_mode=StreamingMode.POLLING,
            ),
            context=context,
            runtime_version=runtime_version,
        )
        self.client = client

    async def health(self) -> WorkerHealth:
        try:
            data = await self.client.health()
            return WorkerHealth(
                worker_id=self.worker_id,
                healthy=bool(data.get("healthy", True)),
                status=str(data.get("status", "healthy")),
                runtime_version=data.get("version") or self.runtime_version,
                adapter_version=self.adapter_api_version,
                details=data,
            )
        except Exception as exc:
            return WorkerHealth(worker_id=self.worker_id, healthy=False, status="unreachable", details={"error": str(exc)})

    async def _execute(self, request: WorkerRunRequest) -> Any:
        result = await self.client.run(request.model_dump(mode="json"))
        if isinstance(result, dict) and "events" in result:
            final: WorkerResult | None = None
            for raw in result["events"]:
                event = WorkerEvent.model_validate({"run_id": request.run_id, "worker_id": self.worker_id, **raw})
                await self._emit(event)
                final = event.result or final
            return final or {"success": True, "output": result}
        return result

    async def cancel(self, request: WorkerCancellation) -> None:
        await self.client.cancel(request.model_dump(mode="json"))
        await super().cancel(request)

    async def close(self) -> None:
        await super().close()
        close = getattr(self.client, "aclose", None)
        if close is not None:
            await close()


class MCPHTTPClient:
    """A small, explicit HTTP bridge for a certified MCP server surface.

    Endpoint paths and the MCP tool name are part of the immutable adapter
    configuration.  This intentionally does not try to guess a server's MCP
    transport or expose its tools directly to a worker.
    """

    def __init__(self, *, base_url: str, endpoints: dict[str, str], headers: dict[str, str] | None = None) -> None:
        required = {"health", "run", "cancel"}
        missing = required - set(endpoints)
        if missing:
            raise ValueError(f"MCP adapter endpoints missing: {', '.join(sorted(missing))}")
        self.endpoints = endpoints
        self.client = httpx.AsyncClient(base_url=base_url.rstrip("/"), headers=headers or {}, timeout=60.0)

    async def health(self) -> dict[str, Any]:
        response = await self.client.get(self.endpoints["health"])
        response.raise_for_status()
        payload = response.json() if response.content else {}
        return payload if isinstance(payload, dict) else {"healthy": True}

    async def run(self, request: dict[str, Any]) -> Any:
        response = await self.client.post(self.endpoints["run"], json=request)
        response.raise_for_status()
        return response.json() if response.content else {"success": True}

    async def cancel(self, request: dict[str, Any]) -> Any:
        response = await self.client.post(self.endpoints["cancel"], json=request)
        response.raise_for_status()
        return response.json() if response.content else {}

    async def aclose(self) -> None:
        await self.client.aclose()


class LangGraphAdapter(NativeWorkerAdapter):
    """Certified native bridge for a LangGraph-compatible callable."""

    runtime_type = "langgraph"


class CrewAIAdapter(NativeWorkerAdapter):
    """Certified native bridge for a CrewAI-compatible callable."""

    runtime_type = "crewai"


class GatewayWorkerAdapter(NativeWorkerAdapter):
    """Run a bounded model-backed worker through AIAT's gateway.

    The adapter owns no provider credentials and never selects a model on its
    own.  The controller supplies an immutable ``resolved_model_profile``;
    readiness rejects requests without an exact model ID, and the normalized
    result records the adapter's configured provider identity for the
    controller's snapshot-attribution check.  A real gateway client may be
    injected for tests or a host-certified deployment; the default client is
    created from an AIAT-owned ``LLMConfig``.
    """

    runtime_type = "aiat_gateway"
    _MAX_GATEWAY_MESSAGES = 64
    _MAX_GATEWAY_CONTENT_CHARS = 32_000

    def __init__(
        self,
        *,
        worker_id: str,
        provider_id: str = "litellm",
        gateway_client: Any | None = None,
        gateway_config: Any | None = None,
        capabilities: WorkerCapabilities | None = None,
        context: AdapterContext | None = None,
        runtime_version: str | None = None,
        max_provider_retries: int = 0,
        retry_min_wait_s: float = 0.0,
        retry_max_wait_s: float = 1.0,
    ) -> None:
        from mas_core.llm_gateway.client import LLMGatewayClient
        from mas_core.llm_gateway.models import LLMConfig

        normalized_provider = str(provider_id).strip()
        if not normalized_provider:
            raise ValueError("gateway worker provider_id must not be blank")
        if not 0 <= int(max_provider_retries) <= 3:
            raise ValueError("max_provider_retries must be between 0 and 3")
        if not 0 <= float(retry_min_wait_s) <= 30:
            raise ValueError("retry_min_wait_s must be between 0 and 30 seconds")
        if not 0 <= float(retry_max_wait_s) <= 60:
            raise ValueError("retry_max_wait_s must be between 0 and 60 seconds")
        if float(retry_max_wait_s) < float(retry_min_wait_s):
            raise ValueError("retry_max_wait_s must be at least retry_min_wait_s")
        owns_gateway_client = gateway_client is None
        if gateway_client is None:
            if gateway_config is None:
                gateway_config = LLMConfig()
            elif isinstance(gateway_config, dict):
                gateway_config = LLMConfig(**gateway_config)
            gateway_client = LLMGatewayClient(gateway_config)
        self.gateway_client = gateway_client
        self.provider_id = normalized_provider
        self._owns_gateway_client = owns_gateway_client
        self._gateway_started = False
        self.max_provider_retries = int(max_provider_retries)
        self.retry_min_wait_s = float(retry_min_wait_s)
        self.retry_max_wait_s = float(retry_max_wait_s)

        async def runner(request: WorkerRunRequest, _adapter: NativeWorkerAdapter) -> WorkerResult:
            return await self._run_gateway(request)

        super().__init__(
            runner,
            worker_id=worker_id,
            capabilities=capabilities
            or _external_capabilities(
                checkpoint_mode=CheckpointMode.UNSUPPORTED,
                model_mode=ModelMode.AIAT_GATEWAY,
                capability_names=["model.chat"],
            ),
            context=context,
            runtime_version=runtime_version,
        )

    async def start(self, request: WorkerRunRequest) -> Any:
        """Start an owned gateway client before accepting the worker run."""

        if self._owns_gateway_client and not self._gateway_started:
            starter = getattr(self.gateway_client, "start", None)
            if callable(starter):
                await starter()
            self._gateway_started = True
        return await super().start(request)

    async def readiness(self, request: WorkerRunRequest | None = None) -> WorkerReadiness:
        local = await super().readiness(request)
        blockers = list(local.blockers)
        checks = dict(local.checks)
        checks["gateway_client_configured"] = self.gateway_client is not None
        checks["resolved_model_profile"] = False
        if request is None or request.resolved_model_profile is None:
            blockers.append("AIAT gateway worker requires a resolved Model Profile")
        else:
            model_id = str(request.resolved_model_profile.exact_model_id or "").strip()
            if not model_id or model_id.lower() == "auto":
                blockers.append("AIAT gateway worker requires an exact resolved model ID")
            else:
                checks["resolved_model_profile"] = True
        return WorkerReadiness(
            worker_id=self.worker_id,
            ready=not blockers,
            checks=checks,
            blockers=blockers,
        )

    @staticmethod
    def _messages_from_request(request: WorkerRunRequest) -> list[dict[str, str]]:
        task_input = request.task_input if isinstance(request.task_input, dict) else {}
        raw_messages = task_input.get("messages")
        if raw_messages is None:
            prompt = task_input.get("prompt")
            if not isinstance(prompt, str) or not prompt.strip():
                raise ValueError("task_input requires a non-empty prompt or messages list")
            content = prompt.strip()
            if len(content) > GatewayWorkerAdapter._MAX_GATEWAY_CONTENT_CHARS:
                raise ValueError("task_input.prompt exceeds the gateway content limit")
            return [{"role": "user", "content": content}]
        if not isinstance(raw_messages, list) or not raw_messages:
            raise ValueError("task_input.messages must be a non-empty list")
        if len(raw_messages) > GatewayWorkerAdapter._MAX_GATEWAY_MESSAGES:
            raise ValueError("task_input.messages exceeds the gateway message limit")
        messages: list[dict[str, str]] = []
        for item in raw_messages:
            if not isinstance(item, dict):
                raise ValueError("each task_input message must be an object")
            role = str(item.get("role") or "").strip()
            content = item.get("content")
            if not role or not isinstance(content, str) or not content.strip():
                raise ValueError("each task_input message requires role and content")
            normalized_content = content.strip()
            if len(normalized_content) > GatewayWorkerAdapter._MAX_GATEWAY_CONTENT_CHARS:
                raise ValueError("task_input message content exceeds the gateway content limit")
            messages.append({"role": role, "content": normalized_content})
        return messages

    @staticmethod
    def _bounded_generation_options(request: WorkerRunRequest) -> tuple[int, float]:
        task_input = request.task_input if isinstance(request.task_input, dict) else {}
        try:
            max_tokens = int(task_input.get("max_tokens", 256))
            temperature = float(task_input.get("temperature", 0.2))
        except (TypeError, ValueError) as exc:
            raise ValueError("max_tokens and temperature must be numeric") from exc
        if not 1 <= max_tokens <= 4096:
            raise ValueError("max_tokens must be between 1 and 4096")
        if not math.isfinite(temperature) or not 0 <= temperature <= 2:
            raise ValueError("temperature must be between 0 and 2")
        return max_tokens, temperature

    async def _run_gateway(self, request: WorkerRunRequest) -> WorkerResult:
        profile = request.resolved_model_profile
        model_id = str(profile.exact_model_id or "").strip() if profile is not None else ""
        try:
            messages = self._messages_from_request(request)
            max_tokens, temperature = self._bounded_generation_options(request)
        except ValueError as exc:
            return WorkerResult(
                run_id=request.run_id,
                worker_id=self.worker_id,
                success=False,
                error=WorkerError(
                    code="MODEL_GATEWAY_INPUT_REJECTED",
                    message="AIAT model gateway input was rejected before dispatch",
                    retryable=False,
                    terminal=True,
                    category="validation",
                    details={"cause_type": type(exc).__name__},
                ),
            )
        from mas_core.llm_gateway.client import (
            RETRYABLE_LLM_STATUS_CODES,
            LLMGatewayError,
        )

        provider_attempts = 0
        last_error: Exception | None = None
        response: Any | None = None
        for attempt in range(self.max_provider_retries + 1):
            provider_attempts = attempt + 1
            try:
                response = await self.gateway_client.chat_completion(
                    messages=messages,
                    model=model_id,
                    max_tokens=max_tokens,
                    temperature=temperature,
                )
                break
            except Exception as exc:
                last_error = exc
                if isinstance(exc, LLMGatewayError):
                    status_code = int(exc.status_code)
                    retryable = status_code in RETRYABLE_LLM_STATUS_CODES or status_code == 0
                else:
                    status_code = 0
                    retryable = True
                if not retryable or attempt >= self.max_provider_retries:
                    break
                wait_s = min(
                    self.retry_max_wait_s,
                    self.retry_min_wait_s * (2**attempt),
                )
                if wait_s > 0:
                    await asyncio.sleep(wait_s)

        if response is None:
            exc = last_error or RuntimeError("gateway dispatch returned no response")
            if isinstance(exc, LLMGatewayError):
                status_code = int(exc.status_code)
                retryable = status_code in RETRYABLE_LLM_STATUS_CODES or status_code == 0
                return WorkerResult(
                    run_id=request.run_id,
                    worker_id=self.worker_id,
                    success=False,
                    error=WorkerError(
                        code=(
                            "MODEL_GATEWAY_TRANSIENT_FAILURE"
                            if retryable
                            else "MODEL_GATEWAY_REQUEST_REJECTED"
                        ),
                        message=(
                            "AIAT model gateway transient failure"
                            if retryable
                            else "AIAT model gateway request was rejected"
                        ),
                        retryable=retryable,
                        terminal=not retryable,
                        category="provider",
                        details={
                            "status_code": status_code,
                            "provider_attempts": provider_attempts,
                            "provider_retry_count": max(0, provider_attempts - 1),
                        },
                        cause_type=type(exc).__name__,
                    ),
                )
            return WorkerResult(
                run_id=request.run_id,
                worker_id=self.worker_id,
                success=False,
                error=WorkerError(
                    code="MODEL_GATEWAY_DISPATCH_FAILED",
                    message="AIAT model gateway dispatch failed",
                    retryable=True,
                    category="provider",
                    details={
                        "cause_type": type(exc).__name__,
                        "provider_attempts": provider_attempts,
                        "provider_retry_count": max(0, provider_attempts - 1),
                    },
                ),
            )
        usage = getattr(response, "usage", None)
        return WorkerResult(
            run_id=request.run_id,
            worker_id=self.worker_id,
            success=True,
            output={
                "text": str(getattr(response, "text", "") or ""),
                "finish_reason": str(getattr(response, "finish_reason", "stop") or "stop"),
            },
            usage=WorkerUsage(
                prompt_tokens=max(0, int(getattr(usage, "prompt_tokens", 0) or 0)),
                completion_tokens=max(0, int(getattr(usage, "completion_tokens", 0) or 0)),
                total_tokens=max(0, int(getattr(usage, "total_tokens", 0) or 0)),
                cost_usd=max(0.0, float(getattr(usage, "estimated_cost_usd", 0.0) or 0.0)),
                provider=self.provider_id,
                exact_model_id=model_id,
            ),
            replay_metadata={
                "adapter_type": self.runtime_type,
                "gateway_backend": str(getattr(getattr(self.gateway_client, "_config", None), "backend", "unknown")),
                "provider_attempts": provider_attempts,
                "provider_retry_count": max(0, provider_attempts - 1),
            },
        )

    async def close(self) -> None:
        await super().close()
        if self._owns_gateway_client and self._gateway_started:
            stop = getattr(self.gateway_client, "stop", None)
            if callable(stop):
                await stop()
            self._gateway_started = False


def _load_certified_callable(reference: str) -> Callable[..., Any]:
    module_name, separator, attribute = reference.partition(":")
    if not separator or not module_name or not attribute or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.]*", module_name) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", attribute):
        raise ValueError("framework adapters require implementation_ref in module:callable form")
    candidate = getattr(importlib.import_module(module_name), attribute, None)
    if not callable(candidate):
        raise ValueError("framework implementation_ref does not resolve to a callable")
    return candidate


def _framework_runner(callable_worker: Callable[..., Any], *, call_style: str) -> Callable[..., Any]:
    async def runner(request: WorkerRunRequest, adapter: NativeWorkerAdapter) -> Any:
        if call_style == "task_input":
            result = callable_worker(request.task_input)
        elif call_style == "request":
            result = callable_worker(request)
        else:
            result = callable_worker(request, adapter)
        if hasattr(result, "__await__"):
            result = await result
        return result

    return runner


class OCIAdapter(ProcessAdapter):
    """OCI adapter using a pinned image digest and a certified launcher."""

    runtime_type = "oci"

    def __init__(
        self,
        image: str,
        *,
        worker_id: str,
        launcher: str = "docker",
        sandbox_profile: str | None = None,
        sandbox_runtime: str = "runsc",
        memory_limit: str = "512m",
        cpu_limit: str = "1.0",
        pids_limit: int = 256,
        **kwargs: Any,
    ) -> None:
        if "@sha256:" not in image:
            raise ValueError("OCI workers must use an immutable image digest")
        if sandbox_profile not in {"gvisor", "firecracker"}:
            raise ValueError("OCI workers require a certified gvisor or firecracker sandbox profile")
        if sandbox_profile == "firecracker":
            raise ValueError("Firecracker OCI execution requires a certified Firecracker launcher")
        if launcher != "docker":
            raise ValueError("gVisor OCI execution currently requires the certified docker/runsc launcher")
        if sandbox_runtime != "runsc":
            raise ValueError("gVisor OCI execution requires the runsc sandbox runtime")
        if pids_limit <= 0:
            raise ValueError("OCI pids_limit must be positive")
        command = [
            launcher,
            "run",
            "--rm",
            "-i",
            "--runtime",
            sandbox_runtime,
            "--network",
            "none",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--read-only",
            "--tmpfs",
            "/tmp:rw,noexec,nosuid,size=64m",
            "--pids-limit",
            str(pids_limit),
            "--memory",
            memory_limit,
            "--cpus",
            cpu_limit,
            image,
        ]
        super().__init__(command, worker_id=worker_id, **kwargs)
        self.image = image
        self.sandbox_profile = sandbox_profile


class FirecrackerAdapter(ProcessAdapter):
    """Run a worker through an AIAT-certified Firecracker launcher.

    The adapter only constructs a validated argv and delegates execution to a
    launcher supplied by the host profile. It never accepts secret values or
    falls back to Docker/runc when the launcher is unavailable.
    """

    runtime_type = "firecracker"

    def __init__(
        self,
        *,
        worker_id: str,
        kernel_path: str,
        kernel_sha256: str,
        rootfs_path: str,
        rootfs_sha256: str,
        artifact_dir: str,
        launcher: str = "aiat-firecracker-launcher",
        network_mode: str = "egress-deny-all",
        egress_allowlist: tuple[str, ...] | list[str] = (),
        vcpu_count: int = 1,
        memory_mib: int = 512,
        pids_limit: int = 256,
        disk_limit_mb: int = 1024,
        output_limit_bytes: int = 4 * 1024 * 1024,
        wall_clock_seconds: int = 300,
        secret_refs: tuple[str, ...] | list[str] = (),
        context: AdapterContext | None = None,
        runtime_version: str | None = None,
    ) -> None:
        spec = FirecrackerLaunchSpec(
            launcher=launcher,
            kernel_path=kernel_path,
            kernel_sha256=kernel_sha256,
            rootfs_path=rootfs_path,
            rootfs_sha256=rootfs_sha256,
            artifact_dir=artifact_dir,
            network_mode=network_mode,  # type: ignore[arg-type]
            egress_allowlist=tuple(egress_allowlist),
            vcpu_count=vcpu_count,
            memory_mib=memory_mib,
            pids_limit=pids_limit,
            disk_limit_mb=disk_limit_mb,
            output_limit_bytes=output_limit_bytes,
            wall_clock_seconds=wall_clock_seconds,
            secret_refs=tuple(secret_refs),
        )
        super().__init__(
            spec.argv(),
            worker_id=worker_id,
            context=context,
            runtime_version=runtime_version,
        )
        self.launch_spec = spec
        self.sandbox_profile = "firecracker"


@dataclass(frozen=True, slots=True)
class OpenCodeInterfaceVerification:
    """Committed evidence required before an OpenCode adapter can run."""

    release: str
    commit_sha: str
    report_version: str
    approved: bool
    openapi_sha256: str | None = None
    config_schema_sha256: str | None = None
    auth_mode: str = "aiat_gateway"
    checkpoint_mode: CheckpointMode = CheckpointMode.RESTART_ONLY
    cancellation_mode: CancellationMode = CancellationMode.COOPERATIVE
    streaming_mode: StreamingMode = StreamingMode.EVENT_STREAM
    supported_model_pattern: str = r"^[^/\s]+/[^/\s]+$"
    endpoints: dict[str, str] = None  # type: ignore[assignment]
    evidence: dict[str, Any] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if not self.release or not self.commit_sha:
            raise ValueError("OpenCode verification requires pinned release and commit")
        if self.auth_mode != "aiat_gateway":
            raise ValueError("OpenCode must use AIAT gateway credentials unless separately certified")
        required_endpoints = {
            "health", "openapi", "project_current", "session_list", "session_create", "session_get",
            "session_delete", "session_status", "messages", "prompt_async", "events",
            "abort", "diff", "permission_reply", "mcp_add", "mcp_status",
        }
        if self.endpoints is None:
            object.__setattr__(self, "endpoints", {})
        if self.approved and required_endpoints <= set(self.endpoints):
            for name, digest in (("openapi_sha256", self.openapi_sha256), ("config_schema_sha256", self.config_schema_sha256)):
                if not digest or not re.fullmatch(r"[0-9a-fA-F]{64}", digest):
                    raise ValueError(f"approved OpenCode verification requires a 64-character {name}")
        if self.approved and required_endpoints - set(self.endpoints):
            raise ValueError("approved OpenCode verification requires the complete pinned session endpoint manifest")
        if self.evidence is None:
            object.__setattr__(self, "evidence", {})

    @classmethod
    def from_report(cls, configured: dict[str, Any]) -> OpenCodeInterfaceVerification:
        """Load only a committed Phase 0B report, never an inline claim.

        ``approved`` is controlled by the reviewed report fixture.  Runtime
        configuration may select a report ID but cannot override its release,
        commit, authentication boundary, endpoint set, or approval decision.
        """
        report_id = str(configured.get("report_id") or "").strip()
        if not report_id or not re.fullmatch(r"[a-z0-9][a-z0-9_.-]*", report_id):
            raise ValueError("OpenCode requires a committed interface_verification.report_id")
        fixture = Path(__file__).with_name("fixtures") / f"{report_id}.json"
        try:
            report = json.loads(fixture.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise ValueError(f"OpenCode interface report {report_id!r} is not committed") from exc
        except json.JSONDecodeError as exc:
            raise ValueError(f"OpenCode interface report {report_id!r} is invalid JSON") from exc
        if report.get("report_id") != report_id:
            raise ValueError("OpenCode interface report ID does not match its fixture")
        if configured.get("release") and configured["release"] != report.get("release"):
            raise ValueError("OpenCode release must match the approved interface report")
        if configured.get("commit_sha") and configured["commit_sha"] != report.get("commit_sha"):
            raise ValueError("OpenCode commit must match the approved interface report")
        fixture_hashes: dict[str, Any] = {}
        approval_ready = bool(report.get("approved")) and report.get("approval_status") == "APPROVED"
        if approval_ready:
            required_evidence = {
                "approval_record_id",
                "config_schema_sha256",
                "openapi_sha256",
                "fixture_refs",
                "fixture_sha256",
            }
            missing = sorted(key for key in required_evidence if not report.get(key))
            if missing:
                raise ValueError(f"OpenCode interface report is missing required evidence: {', '.join(missing)}")
            required_sanitized_fixtures = {
                "event-fixtures/live-event-summary.json",
                "request-response-fixtures/live-interface-summary.json",
            }
            fixture_refs = set(report.get("fixture_refs") or ())
            raw_fixture_hashes = report.get("fixture_sha256")
            if not required_sanitized_fixtures <= fixture_refs or not isinstance(raw_fixture_hashes, dict):
                raise ValueError("OpenCode interface report is missing required sanitized live fixtures")
            invalid_fixture_hashes = sorted(
                reference
                for reference in required_sanitized_fixtures
                if not isinstance(raw_fixture_hashes.get(reference), str)
                or not re.fullmatch(r"[0-9a-fA-F]{64}", raw_fixture_hashes[reference])
            )
            if invalid_fixture_hashes:
                raise ValueError(
                    "OpenCode interface report has invalid sanitized fixture hashes: "
                    + ", ".join(invalid_fixture_hashes)
                )
            fixture_hashes = dict(raw_fixture_hashes)
        raw_endpoints = dict(report.get("endpoints") or {})
        endpoints = {
            key: (value.get("path") if isinstance(value, dict) else value)
            for key, value in raw_endpoints.items()
            if isinstance(value, (str, dict)) and (isinstance(value, str) or isinstance(value.get("path"), str))
        }
        return cls(
            release=str(report["release"]),
            commit_sha=str(report["commit_sha"]),
            report_version=str(report.get("report_version") or "1"),
            approved=bool(report.get("approved")) and report.get("approval_status") == "APPROVED",
            openapi_sha256=str(report["openapi_sha256"]),
            config_schema_sha256=str(report["config_schema_sha256"]),
            auth_mode=str(report.get("auth_mode") or ""),
            checkpoint_mode=CheckpointMode(str(report.get("checkpoint_mode") or "unsupported")),
            cancellation_mode=CancellationMode(str(report.get("cancellation_mode") or "cooperative")),
            streaming_mode=StreamingMode(str(report.get("streaming_mode") or "final_only")),
            supported_model_pattern=str(report.get("supported_model_pattern") or r"^[^/\s]+/[^/\s]+$"),
            endpoints=endpoints,
            evidence={
                "report_id": report_id,
                "approval_record_id": report["approval_record_id"],
                "fixture_refs": list(report["fixture_refs"]),
                "fixture_sha256": dict(fixture_hashes),
            },
        )

    def validate_model_id(self, model_id: str) -> bool:
        return bool(re.fullmatch(self.supported_model_pattern, model_id))


class OpenCodeAdapter(HTTPAdapter):
    """Certified session-oriented OpenCode adapter.

    OpenCode is deliberately treated as a remote runtime, not as an AIAT
    authority.  One WorkerRun owns one server session.  The operational paths,
    request shapes, and schema digest come from the committed Phase 0B report;
    worker configuration can only select that report and a base URL.
    """

    runtime_type = "opencode"
    _MCP_BRIDGE_URL = "http://tool-service:8002/opencode/mcp"
    _MCP_GRANT_TTL_SECONDS = 300
    _MCP_GRANT_REFRESH_LEEWAY_SECONDS = 30
    _MCP_GRANT_REFRESH_RETRY_SECONDS = 5

    def __init__(
        self,
        verification: OpenCodeInterfaceVerification,
        *,
        base_url: str,
        worker_id: str,
        model_mapper: Callable[[str], str] | None = None,
        endpoints: dict[str, str] | None = None,
        **kwargs: Any,
    ) -> None:
        if not verification.approved:
            raise ValueError("OpenCode adapter requires an approved interface verification report")
        if endpoints is not None and endpoints != verification.endpoints:
            raise ValueError("OpenCode endpoint paths must exactly match the approved interface verification report")
        capabilities = kwargs.pop("capabilities", None) or _external_capabilities(
            checkpoint_mode=verification.checkpoint_mode,
            cancellation_mode=verification.cancellation_mode,
            streaming_mode=verification.streaming_mode,
            model_mode=ModelMode.AIAT_GATEWAY,
            capability_names=["opencode.session", "opencode.events", "opencode.artifacts"],
        )
        context = kwargs.pop("context", None) or AdapterContext()
        # A literal password in adapter configuration is never accepted.  A
        # deployment injects this value through the secret boundary into the
        # AdapterContext; tests may use the same explicit secret mapping.
        username = str(context.secrets.get("opencode_username") or "opencode")
        password = str(context.secrets.get("opencode_password") or "")
        if not password:
            raise ValueError("OpenCode requires a password from the AIAT secret boundary")
        self._auth_username = username
        self._auth_password = password
        self._session_by_run: dict[UUID, str] = {}
        self._session_by_key: dict[str, str] = {}
        self._submitted: set[UUID] = set()
        self._seen_runtime_events: set[str] = set()
        self._cancelled: set[UUID] = set()
        self._event_tasks: dict[UUID, asyncio.Task[Any]] = {}
        self._last_messages: dict[UUID, list[dict[str, Any]]] = {}
        self._session_announced: set[UUID] = set()
        self._permission_request_ids: dict[tuple[UUID, str], UUID] = {}
        self._permission_by_run: dict[UUID, tuple[str, str]] = {}
        self._permission_by_request: dict[UUID, tuple[str, str]] = {}
        self._idle: dict[UUID, bool] = {}
        self._terminal_error: dict[UUID, bool] = {}
        self._stop_events: dict[UUID, bool] = {}
        self._mcp_by_run: dict[UUID, str] = {}
        self._mcp_grant_expires_at: dict[UUID, float] = {}
        self._mcp_refresh_tasks: dict[UUID, asyncio.Task[None]] = {}
        self._mcp_locks: dict[UUID, asyncio.Lock] = {}
        super().__init__(
            base_url,
            worker_id=worker_id,
            endpoints=verification.endpoints,
            capabilities=capabilities,
            context=context,
            runtime_version=verification.release,
            headers={"Accept": "application/json"},
            timeout_seconds=float(kwargs.pop("timeout_seconds", 60.0)),
            **kwargs,
        )
        if self._client is not None:
            # Even an injected transport (used by certification tests) must
            # carry the same native Basic Auth boundary as the owned client.
            self._client.auth = httpx.BasicAuth(self._auth_username, self._auth_password)
        self.verification = verification
        self.model_mapper = model_mapper or (lambda model_id: model_id)

    @staticmethod
    def _redact(value: str) -> str:
        return "[REDACTED]" if value else ""

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None and self._owned_client is None:
            self._owned_client = httpx.AsyncClient(
                base_url=self.base_url,
                auth=httpx.BasicAuth(self._auth_username, self._auth_password),
                headers=self.headers,
                timeout=httpx.Timeout(self.timeout_seconds, connect=min(self.timeout_seconds, 10.0)),
                follow_redirects=False,
            )
        return await super()._get_client()

    def _query(self, request: WorkerRunRequest) -> dict[str, str]:
        # Workspace scope is injected by the governed adapter context.  A task
        # payload must never be able to redirect an OpenCode session to an
        # arbitrary host path.
        directory = self.context.workspace_path
        return {"directory": str(directory)} if directory else {}

    def _endpoint(self, key: str) -> str:
        try:
            return self.verification.endpoints[key]
        except KeyError as exc:
            raise RuntimeError(f"OpenCode certified manifest has no {key!r} endpoint") from exc

    def _acceptance_runtime_run_id(self, request: WorkerRunRequest) -> str | None:
        return self._session_by_run.get(request.run_id)

    def _acceptance_metadata(self, request: WorkerRunRequest) -> dict[str, Any]:
        session_id = self._session_by_run.get(request.run_id)
        return {
            "opencode_session_id": session_id,
            "checkpoint_mode": self.verification.checkpoint_mode.value,
        } if session_id else {}

    async def health(self) -> WorkerHealth:
        try:
            client = await self._get_client()
            response = await client.get(self._endpoint("health"))
            response.raise_for_status()
            data = response.json() if response.content else {}
            return WorkerHealth(
                worker_id=self.worker_id,
                healthy=True,
                status=str(data.get("status", "healthy")) if isinstance(data, dict) else "healthy",
                runtime_version=self.runtime_version,
                adapter_version=self.adapter_api_version,
                details={"auth": "basic", "schema_sha256": self.verification.openapi_sha256},
            )
        except Exception as exc:
            return WorkerHealth(
                worker_id=self.worker_id,
                healthy=False,
                status="unreachable",
                runtime_version=self.runtime_version,
                adapter_version=self.adapter_api_version,
                details={"error": type(exc).__name__},
            )

    async def _schema_matches(self) -> bool:
        client = await self._get_client()
        response = await client.get(self._endpoint("openapi"))
        response.raise_for_status()
        document = response.json()
        if not isinstance(document, dict) or not isinstance(document.get("paths"), dict):
            return False
        canonical = json.dumps(_sanitize_openapi_value(document), sort_keys=True, separators=(",", ":")).encode()
        return hashlib.sha256(canonical).hexdigest() == self.verification.openapi_sha256

    async def readiness(self, request: WorkerRunRequest | None = None) -> WorkerReadiness:
        local = await BaseWorkerAdapter.readiness(self, request)
        if not local.ready:
            return local
        health = await self.health()
        blockers = list(local.blockers)
        if not health.healthy:
            blockers.append("OpenCode authenticated health check failed")
        try:
            schema_matches = await self._schema_matches()
        except Exception:
            schema_matches = False
        if not schema_matches:
            blockers.append("OpenCode live OpenAPI schema does not match the certified digest")
        if request is not None:
            if request.resolved_model_profile is None:
                blockers.append("OpenCode requires an AIAT-resolved ModelProfile")
            else:
                model_id = request.resolved_model_profile.exact_model_id
                if not model_id:
                    blockers.append("OpenCode requires an exact provider-qualified model ID")
                elif not self.verification.validate_model_id(model_id):
                    blockers.append("OpenCode model ID must be provider/model qualified")
        return WorkerReadiness(
            worker_id=self.worker_id,
            ready=not blockers,
            checks={"adapter_open": True, "authenticated_health": health.healthy, "schema_pinned": schema_matches},
            blockers=blockers,
        )

    async def _create_session(self, request: WorkerRunRequest) -> str:
        existing = self._session_by_key.get(request.idempotency_key)
        if existing:
            return existing
        bridge_name = await self._configure_tool_bridge(request)
        client = await self._get_client()
        # A persisted session ID may only come from the control-plane adapter
        # context (for restart reconciliation), never from task JSON or user
        # supplied runtime extensions.
        persisted = self.context.metadata.get("opencode_session_id")
        persisted_run_id = self.context.metadata.get("opencode_session_run_id")
        if persisted_run_id is not None and str(persisted_run_id) != str(request.run_id):
            persisted = None
        if isinstance(persisted, str) and re.fullmatch(r"ses[a-zA-Z0-9_-]+", persisted):
            path = self._endpoint("session_get").replace("{sessionID}", persisted)
            response = await client.get(path, params=self._query(request))
            if response.status_code == 200:
                self._session_by_key[request.idempotency_key] = persisted
                self._session_by_run[request.run_id] = persisted
                return persisted
        title = f"AIAT {request.task_type} {request.run_id} [{request.idempotency_key}]"
        # Reconcile a timed-out create response before issuing another POST.
        # The idempotency key is carried in the title and metadata because the
        # 1.17.13 session API has no idempotency header of its own.
        try:
            listed = await client.get(self._endpoint("session_list"), params=self._query(request))
            if listed.status_code == 200 and isinstance(listed.json(), list):
                for candidate in listed.json():
                    if isinstance(candidate, dict) and candidate.get("title") == title and str(candidate.get("id", "")).startswith("ses"):
                        session_id = str(candidate["id"])
                        self._session_by_key[request.idempotency_key] = session_id
                        self._session_by_run[request.run_id] = session_id
                        return session_id
        except (httpx.HTTPError, ValueError):
            pass
        payload: dict[str, Any] = {
            "title": title,
            "metadata": {
                "aiat_worker_run_id": str(request.run_id),
                "aiat_idempotency_key": request.idempotency_key,
                "aiat_worker_id": self.worker_id,
            },
            # OpenCode's native filesystem/shell/web tools are not an AIAT
            # tool bridge.  Start every session deny-by-default; a certified
            # run-scoped MCP bridge may be added by the immutable runtime
            # config and will still surface permission requests to AIAT.
            # OpenCode resolves the last matching permission rule.  Native
            # tools remain denied while the single run-scoped MCP facade is
            # explicitly allowed.
            "permission": [
                {"permission": "*", "pattern": "*", "action": "deny"},
                {
                    "permission": f"{bridge_name}_aiat_tool",
                    "pattern": "*",
                    "action": "allow",
                },
            ],
        }
        model_id = request.resolved_model_profile.exact_model_id if request.resolved_model_profile else None
        if model_id:
            mapped = self.model_mapper(model_id)
            provider, separator, model = mapped.partition("/")
            if not separator or not provider or not model:
                raise ValueError("OpenCode requires provider/model")
            payload["model"] = {"providerID": provider, "id": model, "variant": request.resolved_model_profile.version or ""}
        response = await client.post(self._endpoint("session_create"), params=self._query(request), json=payload)
        response.raise_for_status()
        data = response.json()
        session_id = str(data.get("id") or "") if isinstance(data, dict) else ""
        if not session_id.startswith("ses"):
            raise RuntimeError("OpenCode session.create returned no valid session ID")
        self._session_by_key[request.idempotency_key] = session_id
        self._session_by_run[request.run_id] = session_id
        return session_id

    async def _configure_tool_bridge(
        self,
        request: WorkerRunRequest,
        *,
        force_refresh: bool = False,
    ) -> str:
        """Install the fixed endpoint with a short-lived run capability.

        The runtime configuration cannot supply either the endpoint or the
        bearer token.  The latter remains in the server-side request only and
        never reaches task input, adapter configuration, events, or evidence.
        """
        lock = self._mcp_locks.setdefault(request.run_id, asyncio.Lock())
        async with lock:
            name = self._mcp_by_run.get(request.run_id)
            expires_at = self._mcp_grant_expires_at.get(request.run_id, 0)
            if (
                name is not None
                and not force_refresh
                and time.time() < expires_at - self._MCP_GRANT_REFRESH_LEEWAY_SECONDS
            ):
                return name

            # Re-posting the same MCP server name replaces its connection in
            # OpenCode, so its next request carries the newly issued grant
            # without changing the permission name pinned into the session.
            issued_at = int(time.time())
            signing_secret = str(self.context.secrets.get("tool_secret") or "")
            grant = issue_opencode_tool_grant(
                signing_secret,
                worker_id=self.worker_id,
                run_id=request.run_id,
                project_id=request.project_id,
                tool_names=request.tool_grants,
                ttl_seconds=self._MCP_GRANT_TTL_SECONDS,
                now=issued_at,
            )
            name = name or f"aiat-{request.run_id.hex}"
            client = await self._get_client()
            response = await client.post(
                self._endpoint("mcp_add"),
                params=self._query(request),
                json={
                    "name": name,
                    "config": {
                        "type": "remote",
                        "url": self._MCP_BRIDGE_URL,
                        "headers": {"X-AIAT-OpenCode-Grant": grant},
                        "oauth": False,
                        "enabled": True,
                        "timeout": 60_000,
                    },
                },
            )
            response.raise_for_status()
            for _ in range(60):
                status_response = await client.get(
                    self._endpoint("mcp_status"),
                    params=self._query(request),
                )
                status_response.raise_for_status()
                states = status_response.json()
                state = states.get(name) if isinstance(states, dict) else None
                if isinstance(state, dict) and state.get("status") == "connected":
                    break
                await asyncio.sleep(0.25)
            else:
                raise RuntimeError("OpenCode run-scoped MCP bridge did not connect")
            self._mcp_by_run[request.run_id] = name
            self._mcp_grant_expires_at[request.run_id] = float(
                issued_at + self._MCP_GRANT_TTL_SECONDS
            )
            return name

    def _ensure_mcp_grant_refresher(self, request: WorkerRunRequest) -> None:
        task = self._mcp_refresh_tasks.get(request.run_id)
        if task is None or task.done():
            self._mcp_refresh_tasks[request.run_id] = asyncio.create_task(
                self._refresh_mcp_grant(request),
                name=f"opencode-mcp-grant-{request.run_id}",
            )

    async def _refresh_mcp_grant(self, request: WorkerRunRequest) -> None:
        """Replace the OpenCode MCP connection before its grant can expire."""
        try:
            while request.run_id in self._mcp_by_run:
                expires_at = self._mcp_grant_expires_at.get(request.run_id)
                if expires_at is None:
                    return
                delay = max(
                    0.0,
                    expires_at - time.time() - self._MCP_GRANT_REFRESH_LEEWAY_SECONDS,
                )
                await asyncio.sleep(delay)
                await self._configure_tool_bridge(request, force_refresh=True)
        except asyncio.CancelledError:
            raise
        except Exception:
            # Never log the token; retrying preserves a still-valid grant when
            # a transient OpenCode connection refresh fails.
            logger.warning(
                "opencode_mcp_grant_refresh_failed",
                extra={"run_id": str(request.run_id), "worker_id": self.worker_id},
            )
            await asyncio.sleep(self._MCP_GRANT_REFRESH_RETRY_SECONDS)
            if request.run_id in self._mcp_by_run:
                self._mcp_refresh_tasks[request.run_id] = asyncio.create_task(
                    self._refresh_mcp_grant(request),
                    name=f"opencode-mcp-grant-{request.run_id}",
                )

    async def _stop_mcp_grant_refresher(self, run_id: UUID) -> None:
        task = self._mcp_refresh_tasks.pop(run_id, None)
        if task is not None and task is not asyncio.current_task():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        self._mcp_by_run.pop(run_id, None)
        self._mcp_grant_expires_at.pop(run_id, None)
        self._mcp_locks.pop(run_id, None)

    async def start(self, request: WorkerRunRequest) -> Any:
        existing = self._accepted_by_key.get(request.idempotency_key)
        if existing is not None:
            return existing
        readiness = await self.readiness(request)
        if not readiness.ready:
            raise RuntimeError("; ".join(readiness.blockers))
        try:
            await self._create_session(request)
        except Exception:
            await self._stop_mcp_grant_refresher(request.run_id)
            raise
        # BaseWorkerAdapter.start creates the task after emitting ACCEPTED;
        # the session map above lets the base hook include the runtime ID in
        # that first authoritative acceptance event.
        try:
            result = await BaseWorkerAdapter.start(self, request)
        except Exception:
            await self._stop_mcp_grant_refresher(request.run_id)
            raise
        self._accepted_by_key[request.idempotency_key] = result
        return result

    @staticmethod
    def _prompt_text(request: WorkerRunRequest) -> str:
        raw = request.task_input.get("prompt") or request.task_input.get("task")
        if isinstance(raw, str) and raw.strip():
            return raw.strip()
        blocked = {"model", "model_id", "provider", "credentials", "api_key", "token", "secret", "password", "workspace_path"}

        def safe(value: Any, key: str = "") -> Any:
            if key.lower() in blocked or any(marker in key.lower() for marker in ("password", "secret", "token", "credential", "api_key")):
                return "[REDACTED]"
            if isinstance(value, dict):
                return {str(child_key): safe(child_value, str(child_key)) for child_key, child_value in value.items() if str(child_key).lower() not in blocked}
            if isinstance(value, list):
                return [safe(item, key) for item in value]
            return value

        return json.dumps(safe(request.task_input), sort_keys=True)

    async def _submit(self, request: WorkerRunRequest, session_id: str) -> None:
        if request.run_id in self._submitted:
            return
        model_id = request.resolved_model_profile.exact_model_id if request.resolved_model_profile else ""
        provider, separator, model = self.model_mapper(model_id).partition("/")
        if not separator:
            raise WorkerError(code="INVALID_MODEL_ID", message="OpenCode requires provider/model", category="policy")
        body: dict[str, Any] = {
            "parts": [{"type": "text", "text": self._prompt_text(request)}],
            "model": {"providerID": provider, "modelID": model},
            "noReply": bool(request.task_input.get("no_reply", False)),
        }
        # Agent selection is part of the immutable adapter/run configuration;
        # task JSON cannot switch to an unreviewed OpenCode agent.
        agent = self.context.metadata.get("opencode_agent")
        if isinstance(agent, str) and agent.strip():
            body["agent"] = agent.strip()
        client = await self._get_client()
        # Path parameters are immutable manifest paths; the only substitution
        # permitted is the server-issued session ID.
        path = self._endpoint("prompt_async").replace("{sessionID}", session_id)
        response = await client.post(path, params=self._query(request), json=body)
        if response.status_code not in {200, 204}:
            response.raise_for_status()
        self._submitted.add(request.run_id)

    @staticmethod
    def _event_key(raw: dict[str, Any]) -> str:
        event_id = raw.get("id")
        if event_id:
            return str(event_id)
        return hashlib.sha256(json.dumps(raw, sort_keys=True, separators=(",", ":")).encode()).hexdigest()

    async def _emit_runtime_event(self, request: WorkerRunRequest, raw: dict[str, Any]) -> None:
        key = f"{request.run_id}:{self._event_key(raw)}"
        if key in self._seen_runtime_events:
            return
        self._seen_runtime_events.add(key)
        payload = raw.get("payload") if isinstance(raw.get("payload"), dict) else raw
        event_type = str(payload.get("type") or "")
        props = payload.get("properties") if isinstance(payload.get("properties"), dict) else {}
        extensions = {"namespace": "opencode", "runtime_event_id": raw.get("id"), "event_type": event_type, "properties": props}
        if event_type == "permission.asked":
            permission_id = str(props.get("id") or "")
            permission = str(props.get("permission") or "opencode.permission")
            tool = WorkerToolRequest(
                run_id=request.run_id,
                tool_name=f"opencode.permission.{permission}",
                arguments={"permission_id": permission_id, "patterns": props.get("patterns", []), "metadata": props.get("metadata", {})},
                permission_scope=[permission],
                approval_required=True,
                idempotency_key=f"{request.idempotency_key}:permission:{permission_id}",
            )
            self._permission_by_run[request.run_id] = (str(props.get("sessionID") or self._session_by_run.get(request.run_id) or ""), permission_id)
            self._permission_by_request[tool.request_id] = self._permission_by_run[request.run_id]
            self._permission_request_ids[(request.run_id, permission_id)] = tool.request_id
            await self._emit(WorkerEvent(protocol=self._protocol(), run_id=request.run_id, worker_id=self.worker_id, event_type=EventType.TOOL_REQUEST, tool_request=tool, extensions=extensions))
        elif event_type == "permission.replied":
            permission_id = str(props.get("id") or props.get("permissionID") or "")
            request_id = self._permission_request_ids.get((request.run_id, permission_id))
            if request_id is not None:
                response_value = str(props.get("response") or props.get("action") or "reject")
                response = WorkerToolResponse(
                    request_id=request_id,
                    run_id=request.run_id,
                    tool_name=f"opencode.permission.{props.get('permission') or 'unknown'}",
                    success=response_value in {"once", "always", "allow", "approved"},
                    result=props,
                )
                await self._emit(WorkerEvent(protocol=self._protocol(), run_id=request.run_id, worker_id=self.worker_id, event_type=EventType.TOOL_RESPONSE, tool_response=response, extensions=extensions))
            else:
                await self._emit(WorkerEvent(protocol=self._protocol(), run_id=request.run_id, worker_id=self.worker_id, event_type=EventType.PROGRESS, progress={"message": "permission reply without a matching request", "phase": "permission"}, extensions=extensions))
        elif event_type == "session.error":
            message = props.get("error") if isinstance(props.get("error"), dict) else props
            await self._emit(WorkerEvent(protocol=self._protocol(), run_id=request.run_id, worker_id=self.worker_id, event_type=EventType.ERROR, error=WorkerError(code="OPENCODE_SESSION_ERROR", message=str(message)[:2000], retryable=True, category="runtime"), extensions=extensions))
            self._terminal_error[request.run_id] = True
        elif event_type == "session.idle":
            self._idle[request.run_id] = True
            await self._emit(WorkerEvent(protocol=self._protocol(), run_id=request.run_id, worker_id=self.worker_id, event_type=EventType.PROGRESS, progress={"message": "OpenCode session idle", "phase": "idle"}, extensions=extensions))
        elif event_type in {"message.updated", "message.part.updated", "message.part.delta"}:
            await self._emit(WorkerEvent(protocol=self._protocol(), run_id=request.run_id, worker_id=self.worker_id, event_type=EventType.PROGRESS, progress={"message": event_type, "phase": "message"}, extensions=extensions))
        elif event_type == "file.edited":
            await self._emit(WorkerEvent(protocol=self._protocol(), run_id=request.run_id, worker_id=self.worker_id, event_type=EventType.PROGRESS, progress={"message": f"edited {props.get('file', '')}", "phase": "artifact"}, extensions=extensions))
        else:
            await self._emit(WorkerEvent(protocol=self._protocol(), run_id=request.run_id, worker_id=self.worker_id, event_type=EventType.PROGRESS, progress={"message": event_type or "unknown OpenCode event", "phase": "runtime"}, extensions=extensions))

    async def _consume_events(self, request: WorkerRunRequest) -> None:
        client = await self._get_client()
        endpoint = self._endpoint("events")
        while not self._stop_events.get(request.run_id, False):
            try:
                async with client.stream("GET", endpoint, headers={"Accept": "text/event-stream"}) as response:
                    response.raise_for_status()
                    data_lines: list[str] = []
                    async for line in response.aiter_lines():
                        if self._stop_events.get(request.run_id, False):
                            break
                        if line.startswith("data:"):
                            data_lines.append(line[5:].strip())
                        elif not line and data_lines:
                            try:
                                raw = json.loads("\n".join(data_lines))
                                if isinstance(raw, dict):
                                    await self._emit_runtime_event(request, raw)
                            except json.JSONDecodeError:
                                await self._emit(WorkerEvent(protocol=self._protocol(), run_id=request.run_id, worker_id=self.worker_id, event_type=EventType.AUDIT, audit={"action": "opencode.malformed_event", "actor": "adapter", "outcome": "rejected"}))
                            data_lines = []
                    if data_lines and not self._stop_events.get(request.run_id, False):
                        try:
                            raw = json.loads("\n".join(data_lines))
                            if isinstance(raw, dict):
                                await self._emit_runtime_event(request, raw)
                        except json.JSONDecodeError:
                            await self._emit(WorkerEvent(protocol=self._protocol(), run_id=request.run_id, worker_id=self.worker_id, event_type=EventType.AUDIT, audit={"action": "opencode.malformed_event", "actor": "adapter", "outcome": "rejected"}))
            except (httpx.HTTPError, asyncio.CancelledError):
                if self._stop_events.get(request.run_id, False):
                    break
                await asyncio.sleep(0.25)
            except Exception:
                if self._stop_events.get(request.run_id, False):
                    break
                await asyncio.sleep(0.25)
            # A closed HTTP response (especially a test transport) can
            # complete without yielding control.  Bound reconnect cadence so
            # the run poller and cancellation path cannot be starved.
            await asyncio.sleep(0.05)

    async def _messages(self, request: WorkerRunRequest, session_id: str) -> list[dict[str, Any]]:
        client = await self._get_client()
        path = self._endpoint("messages").replace("{sessionID}", session_id)
        response = await client.get(path, params=self._query(request))
        response.raise_for_status()
        data = response.json()
        return data if isinstance(data, list) else []

    async def _session_status(self, request: WorkerRunRequest, session_id: str) -> dict[str, Any] | None:
        client = await self._get_client()
        response = await client.get(self._endpoint("session_status"), params=self._query(request))
        if response.status_code >= 400:
            return None
        payload = response.json()
        status = payload.get(session_id) if isinstance(payload, dict) else None
        return status if isinstance(status, dict) else None

    async def _artifacts(self, request: WorkerRunRequest, session_id: str) -> list[Any]:
        client = await self._get_client()
        path = self._endpoint("diff").replace("{sessionID}", session_id)
        response = await client.get(path, params=self._query(request))
        if response.status_code >= 400:
            return []
        diffs = response.json() if response.content else []
        artifacts = []
        workspace = self.context.workspace_path
        if not workspace or not isinstance(diffs, list):
            return artifacts
        root = Path(str(workspace)).resolve()
        from mas_core.worker_contract import ArtifactKind, WorkerArtifact
        for item in diffs:
            rel = str(item.get("file") or item.get("path") or "") if isinstance(item, dict) else ""
            if not rel:
                continue
            candidate = (root / rel).resolve()
            try:
                candidate.relative_to(root)
            except ValueError:
                continue
            if candidate.is_file():
                digest = hashlib.sha256(candidate.read_bytes()).hexdigest()
                artifact = WorkerArtifact(kind=ArtifactKind.FILE, name=rel, uri=rel, sha256=digest, size_bytes=candidate.stat().st_size, metadata={"opencode_session_id": session_id})
                artifacts.append(artifact)
                if self.context.artifact_registrar is not None:
                    await self.context.artifact_registrar(artifact)
        return artifacts

    async def _cleanup_session(self, request: WorkerRunRequest, session_id: str) -> None:
        if not self.context.metadata.get("opencode_cleanup_sessions"):
            return
        client = await self._get_client()
        path = self._endpoint("session_delete").replace("{sessionID}", session_id)
        response = await client.delete(path, params=self._query(request))
        if response.status_code not in {200, 404}:
            response.raise_for_status()

    async def _execute(self, request: WorkerRunRequest) -> Any:
        session_id = self._session_by_run.get(request.run_id) or await self._create_session(request)
        # A restart/retry may reuse a known session.  Ensure that path also
        # recreates the refresher if the previous task was interrupted.
        try:
            await self._configure_tool_bridge(request)
        except Exception:
            await self._stop_mcp_grant_refresher(request.run_id)
            raise
        self._ensure_mcp_grant_refresher(request)
        self._idle[request.run_id] = False
        self._terminal_error[request.run_id] = False
        self._stop_events[request.run_id] = False
        self._permission_by_run.setdefault(request.run_id, (session_id, ""))
        await self._emit(WorkerEvent(protocol=self._protocol(), run_id=request.run_id, worker_id=self.worker_id, event_type=EventType.STARTED, extensions={"namespace": "opencode", "session_id": session_id}))
        if request.run_id not in self._session_announced:
            self._session_announced.add(request.run_id)
            await self._emit(WorkerEvent(protocol=self._protocol(), run_id=request.run_id, worker_id=self.worker_id, event_type=EventType.PROGRESS, progress={"message": "OpenCode session created", "phase": "session"}, extensions={"namespace": "opencode", "session_id": session_id, "event_type": "session.created"}))
        self._event_tasks[request.run_id] = asyncio.create_task(self._consume_events(request), name=f"opencode-events-{request.run_id}")
        started = time.monotonic()
        try:
            await self._submit(request, session_id)
            while True:
                if request.run_id in self._cancelled or request.run_id in self._cancel_requested:
                    return WorkerResult(run_id=request.run_id, worker_id=self.worker_id, success=False, error=WorkerError(code="CANCELLED", message="OpenCode session aborted by AIAT", retryable=False, terminal=True, category="cancellation"), replay_metadata={"opencode_session_id": session_id, "checkpoint_mode": self.verification.checkpoint_mode.value})
                if self._terminal_error.get(request.run_id):
                    return WorkerResult(run_id=request.run_id, worker_id=self.worker_id, success=False, error=WorkerError(code="OPENCODE_SESSION_ERROR", message="OpenCode reported a session error", retryable=True, category="runtime"), replay_metadata={"opencode_session_id": session_id})
                messages = await self._messages(request, session_id)
                self._last_messages[request.run_id] = messages
                status = await self._session_status(request, session_id)
                if isinstance(status, dict) and status.get("type") == "idle":
                    self._idle[request.run_id] = True
                if self._idle.get(request.run_id) and time.monotonic() - started >= 0.5:
                    output_parts: list[str] = []
                    prompt_tokens = completion_tokens = total_tokens = 0
                    cost_usd = 0.0
                    for item in messages:
                        info = item.get("info") if isinstance(item, dict) else {}
                        if isinstance(info, dict) and info.get("role") == "assistant":
                            tokens = info.get("tokens") if isinstance(info.get("tokens"), dict) else {}
                            prompt_tokens += int(tokens.get("input") or tokens.get("prompt") or info.get("prompt_tokens") or 0)
                            completion_tokens += int(tokens.get("output") or tokens.get("completion") or info.get("completion_tokens") or 0)
                            total_tokens += int(tokens.get("total") or info.get("total_tokens") or 0)
                            cost_usd += float(info.get("cost") or info.get("cost_usd") or 0)
                            for part in item.get("parts") or []:
                                if isinstance(part, dict) and part.get("type") == "text" and isinstance(part.get("text"), str):
                                    output_parts.append(part["text"])
                    artifacts = await self._artifacts(request, session_id)
                    return WorkerResult(run_id=request.run_id, worker_id=self.worker_id, success=True, output="\n".join(output_parts), artifacts=artifacts, usage=WorkerUsage(prompt_tokens=prompt_tokens, completion_tokens=completion_tokens, total_tokens=total_tokens, cost_usd=cost_usd, exact_model_id=request.resolved_model_profile.exact_model_id if request.resolved_model_profile else None, provider=(request.resolved_model_profile.exact_model_id.split("/", 1)[0] if request.resolved_model_profile and request.resolved_model_profile.exact_model_id else None), duration_ms=(time.monotonic() - started) * 1000), replay_metadata={"opencode_session_id": session_id, "opencode_release": self.verification.release, "schema_sha256": self.verification.openapi_sha256, "checkpoint_mode": self.verification.checkpoint_mode.value})
                if request.timeout_seconds and time.monotonic() - started > request.timeout_seconds:
                    raise TimeoutError("OpenCode run exceeded adapter timeout")
                await asyncio.sleep(0.5)
        finally:
            self._stop_events[request.run_id] = True
            task = self._event_tasks.pop(request.run_id, None)
            if task is not None:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            await self._stop_mcp_grant_refresher(request.run_id)
            await self._cleanup_session(request, session_id)

    async def cancel(self, request: WorkerCancellation) -> None:
        session_id = self._session_by_run.get(request.run_id)
        if session_id:
            client = await self._get_client()
            path = self._endpoint("abort").replace("{sessionID}", session_id)
            try:
                response = await client.post(path, params=self._query(WorkerRunRequest(run_id=request.run_id, idempotency_key="cancel", worker_id=self.worker_id, task_type="cancel")))
                response.raise_for_status()
            except httpx.HTTPError:
                if not request.force:
                    raise
            self._cancelled.add(request.run_id)
            await self._stop_mcp_grant_refresher(request.run_id)
        await super().cancel(request)

    async def deliver_tool_response(self, response: Any) -> None:
        await super().deliver_tool_response(response)
        session_id, permission_id = self._permission_by_request.get(
            response.request_id,
            self._permission_by_run.get(response.run_id, ("", "")),
        )
        if not session_id or not permission_id:
            return
        client = await self._get_client()
        path = self._endpoint("permission_reply").replace("{sessionID}", session_id).replace("{permissionID}", permission_id)
        await client.post(path, json={"response": "once" if response.success else "reject"})

    async def close(self) -> None:
        self._stop_events.update({run_id: True for run_id in self._event_tasks})
        await asyncio.gather(
            *(self._stop_mcp_grant_refresher(run_id) for run_id in list(self._mcp_refresh_tasks)),
            return_exceptions=True,
        )
        await super().close()
        if self._owned_client is not None:
            await self._owned_client.aclose()
            self._owned_client = None


def adapter_for_transport(
    transport: str,
    *,
    worker_id: str,
    config: dict[str, Any],
    context: AdapterContext | None = None,
) -> BaseWorkerAdapter:
    """Construct only a certified transport adapter from explicit config."""

    normalized = transport.lower()
    if normalized == "process":
        return ProcessAdapter(config.get("command") or [config.get("entrypoint", "worker")], worker_id=worker_id, context=context, cwd=config.get("cwd"), environment=config.get("environment"))
    if normalized == "http":
        return HTTPAdapter(config["base_url"], worker_id=worker_id, context=context, endpoints=config.get("endpoints"), headers=config.get("headers"))
    if normalized == "mcp":
        mcp = MCPHTTPClient(
            base_url=config["base_url"],
            endpoints=dict(config.get("endpoints") or {}),
            headers=config.get("headers"),
        )
        return MCPAdapter(mcp, worker_id=worker_id, context=context)
    if normalized == "oci":
        if str(config.get("sandbox_profile") or "").strip().lower() == "firecracker":
            return FirecrackerAdapter(
                worker_id=worker_id,
                context=context,
                launcher=str(config.get("launcher") or "aiat-firecracker-launcher"),
                kernel_path=str(config["kernel_path"]),
                kernel_sha256=str(config["kernel_sha256"]),
                rootfs_path=str(config["rootfs_path"]),
                rootfs_sha256=str(config["rootfs_sha256"]),
                artifact_dir=str(config["artifact_dir"]),
                network_mode=str(config.get("network_mode") or "egress-deny-all"),
                egress_allowlist=tuple(config.get("egress_allowlist") or ()),
                vcpu_count=int(config.get("vcpu_count", 1)),
                memory_mib=int(config.get("memory_mib", 512)),
                pids_limit=int(config.get("pids_limit", 256)),
                disk_limit_mb=int(config.get("disk_limit_mb", 1024)),
                output_limit_bytes=int(config.get("output_limit_bytes", 4 * 1024 * 1024)),
                wall_clock_seconds=int(config.get("wall_clock_seconds", 300)),
                secret_refs=tuple(config.get("secret_refs") or ()),
                runtime_version=config.get("runtime_version"),
            )
        return OCIAdapter(
            config["image"],
            worker_id=worker_id,
            context=context,
            launcher=config.get("launcher", "docker"),
            sandbox_profile=config.get("sandbox_profile"),
            sandbox_runtime=config.get("sandbox_runtime", "runsc"),
            memory_limit=config.get("memory_limit", "512m"),
            cpu_limit=config.get("cpu_limit", "1.0"),
            pids_limit=int(config.get("pids_limit", 256)),
        )
    if normalized in {"aiat_gateway", "gateway"}:
        from mas_core.llm_gateway.models import LLMConfig

        context_secrets = context.secrets if context is not None else {}
        gateway_config = LLMConfig(
            gateway_url=str(config.get("gateway_url") or "http://litellm:4000"),
            default_model=str(config.get("default_model") or "auto"),
            api_key=str(
                context_secrets.get("llm_api_key")
                or context_secrets.get("LLM_API_KEY")
                or ""
            ),
            backend=str(config.get("backend") or "litellm"),
            timeout_s=float(config.get("timeout_s", 120.0)),
            max_retries=int(config.get("max_retries", 3)),
        )
        return GatewayWorkerAdapter(
            worker_id=worker_id,
            provider_id=str(config.get("provider_id") or "litellm"),
            gateway_config=gateway_config,
            context=context,
            runtime_version=config.get("runtime_version"),
        )
    if normalized in {"openhands", "openhands_agent_server"}:
        from mas_core.worker_registry.openhands_agent_server_adapter import (
            OpenHandsAgentServerAdapter,
            OpenHandsInterfaceVerification,
        )

        report = config.get("interface_verification")
        report_ref = config.get("interface_verification_ref")
        if report is None and report_ref:
            report_path = Path(str(report_ref))
            if not report_path.is_absolute() and not report_path.is_file():
                # Manifests are repository-relative, while the worker service
                # may be launched from ``mas/`` or another application cwd.
                # Resolve against the repository root without accepting a
                # missing or silently substituted report.
                repo_root = Path(__file__).resolve().parents[5]
                candidate = repo_root / report_path
                if candidate.is_file():
                    report_path = candidate
            report = report_path
        if report is None:
            raise ValueError("OpenHands transport requires pinned interface verification evidence")
        verification = OpenHandsInterfaceVerification.from_report(report)
        return OpenHandsAgentServerAdapter(
            verification,
            base_url=str(config.get("base_url") or ""),
            worker_id=worker_id,
            context=context,
            timeout_seconds=float(config.get("timeout_seconds", 60.0)),
        )
    if normalized in {"native", "langgraph", "crewai"}:
        reference = str(config.get("implementation_ref") or config.get("entrypoint") or "")
        worker = _framework_runner(
            _load_certified_callable(reference),
            call_style=str(config.get("call_style") or "request_adapter"),
        )
        adapter_type = {
            "native": NativeWorkerAdapter,
            "langgraph": LangGraphAdapter,
            "crewai": CrewAIAdapter,
        }[normalized]
        return adapter_type(worker, worker_id=worker_id, context=context)
    raise ValueError(f"transport {transport!r} requires a runtime-specific certified client")

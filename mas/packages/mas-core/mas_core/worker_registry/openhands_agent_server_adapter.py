"""Inactive OpenHands Agent Server candidate adapter.

This module is deliberately separate from the OpenCode adapter.  It translates
the pinned OpenHands Agent Server HTTP/WebSocket surface into the universal
AIAT worker contract, while leaving worker activation and all authority in the
AIAT controller.  The adapter is not registered in the active runtime catalog
until the candidate completes certification.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID

import httpx

from mas_core.worker_contract import (
    AdapterContext,
    ArtifactKind,
    BaseWorkerAdapter,
    CancellationMode,
    CheckpointMode,
    MemoryMode,
    ModelMode,
    StreamingMode,
    ToolMode,
    WorkerArtifact,
    WorkerCancellation,
    WorkerCapabilities,
    WorkerError,
    WorkerHealth,
    WorkerPause,
    WorkerReadiness,
    WorkerResult,
    WorkerResume,
    WorkerRunRequest,
    WorkerUsage,
)

DEFAULT_ENDPOINTS: dict[str, str] = {
    "health": "/health",
    "readiness": "/ready",
    "server_info": "/server_info",
    "conversation_create": "/api/conversations",
    "conversation_get": "/api/conversations/{conversation_id}",
    "conversation_run": "/api/conversations/{conversation_id}/run",
    "conversation_pause": "/api/conversations/{conversation_id}/pause",
    "conversation_interrupt": "/api/conversations/{conversation_id}/interrupt",
    "conversation_delete": "/api/conversations/{conversation_id}",
    "agent_final_response": "/api/conversations/{conversation_id}/agent_final_response",
    "events_search": "/api/conversations/{conversation_id}/events/search",
    "git_changes": "/api/git/changes",
    "file_download": "/api/file/download",
    "events_socket": "/sockets/events/{conversation_id}",
}

TERMINAL_STATUSES = frozenset({"finished", "error", "stuck"})


@dataclass(frozen=True, slots=True)
class OpenHandsInterfaceVerification:
    """Pinned interface evidence selected by an operator/steward.

    ``approved`` is intentionally false for the committed candidate report.
    Constructing an executable adapter requires a separate approval record;
    a version pin by itself is never activation evidence.
    """

    report_id: str
    release: str
    commit_sha: str
    repository: str
    image_ref: str
    image_digest: str
    image_platform_digest: str | None
    endpoints: dict[str, str]
    approved: bool
    approval_record_id: str | None = None
    evidence: dict[str, Any] | None = None

    @classmethod
    def from_report(cls, report: str | Path | dict[str, Any]) -> OpenHandsInterfaceVerification:
        if isinstance(report, (str, Path)):
            payload = json.loads(Path(report).read_text(encoding="utf-8"))
        else:
            payload = report
        if not isinstance(payload, dict):
            raise ValueError("OpenHands interface report must be an object")
        pin = payload.get("pin") if isinstance(payload.get("pin"), dict) else {}
        image = payload.get("image") if isinstance(payload.get("image"), dict) else {}
        release = str(pin.get("release") or payload.get("release") or "").strip()
        commit_sha = str(pin.get("commit_sha") or payload.get("commit_sha") or "").strip()
        repository = str(pin.get("repository") or payload.get("repository") or "").strip()
        image_ref = str(image.get("ref") or payload.get("image_ref") or "").strip()
        image_digest = str(image.get("digest") or payload.get("image_digest") or "").strip()
        platform_digest = image.get("amd64_digest") or image.get("image_platform_digest")
        if not release or not commit_sha or len(commit_sha) != 40 or not repository:
            raise ValueError("OpenHands report must pin release, repository, and full commit SHA")
        if not image_ref or not image_digest.startswith("sha256:"):
            raise ValueError("OpenHands report must pin an OCI image digest")
        endpoints = dict(DEFAULT_ENDPOINTS)
        declared = payload.get("endpoints")
        if isinstance(declared, dict):
            for name, value in declared.items():
                path = value.get("path") if isinstance(value, dict) else value
                if isinstance(path, str) and path.startswith("/"):
                    endpoints[str(name)] = path
        approval_status = str(payload.get("approval_status") or "").upper()
        return cls(
            report_id=str(payload.get("report_id") or "openhands-interface-report"),
            release=release,
            commit_sha=commit_sha,
            repository=repository,
            image_ref=image_ref,
            image_digest=image_digest,
            image_platform_digest=str(platform_digest) if platform_digest else None,
            endpoints=endpoints,
            approved=bool(payload.get("approved")) and approval_status == "APPROVED",
            approval_record_id=(str(payload["approval_record_id"]) if payload.get("approval_record_id") else None),
            evidence=payload.get("evidence") if isinstance(payload.get("evidence"), dict) else {},
        )

    def endpoint(self, name: str, **values: str) -> str:
        try:
            path = self.endpoints[name]
        except KeyError as exc:
            raise RuntimeError(f"OpenHands report has no {name!r} endpoint") from exc
        for key, value in values.items():
            path = path.replace("{" + key + "}", value)
        return path


def _capabilities() -> WorkerCapabilities:
    return WorkerCapabilities(
        checkpoint_mode=CheckpointMode.WRAPPER,
        cancellation_mode=CancellationMode.IMMEDIATE,
        streaming_mode=StreamingMode.EVENT_STREAM,
        tool_mode=ToolMode.AIAT_MEDIATED,
        memory_mode=MemoryMode.AIAT,
        workspace_mode="isolated",
        model_mode=ModelMode.AIAT_GATEWAY,
        capability_names=[
            "openhands.conversation",
            "openhands.websocket_events",
            "openhands.workspace_files",
            "openhands.git_changes",
            "openhands.pause",
            "openhands.interrupt",
        ],
    )


class OpenHandsAgentServerAdapter(BaseWorkerAdapter):
    """Parallel candidate adapter for a pinned OpenHands Agent Server.

    The server-side agent profile must already contain the approved model and
    AIAT MCP bridge.  Task input can provide a prompt only; it cannot select a
    workspace, agent profile, model, credentials, or external tools.
    """

    runtime_type = "openhands_agent_server"

    def __init__(
        self,
        verification: OpenHandsInterfaceVerification,
        *,
        base_url: str,
        worker_id: str,
        client: httpx.AsyncClient | None = None,
        context: AdapterContext | None = None,
        timeout_seconds: float = 60.0,
    ) -> None:
        if not verification.approved:
            raise ValueError("OpenHands adapter requires an approved interface verification report")
        if not base_url or not urlsplit(base_url).scheme:
            raise ValueError("OpenHands Agent Server base URL is required")
        context = context or AdapterContext()
        session_key = str(context.secrets.get("openhands_session_api_key") or "")
        if not session_key:
            raise ValueError("OpenHands requires a session API key from the AIAT secret boundary")
        super().__init__(
            worker_id=worker_id,
            capabilities=_capabilities(),
            context=context,
            runtime_version=verification.release,
        )
        self.verification = verification
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self._session_key = session_key
        self._client = client
        self._owned_client: httpx.AsyncClient | None = None
        self._conversation_by_key: dict[str, str] = {}
        self._conversation_by_run: dict[UUID, str] = {}
        self._event_tasks: dict[UUID, asyncio.Task[Any]] = {}
        self._stop_events: set[UUID] = set()
        self._cancelled: set[UUID] = set()

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is not None:
            self._client.headers.setdefault("X-Session-API-Key", self._session_key)
            self._client.headers.setdefault("Accept", "application/json")
            return self._client
        if self._owned_client is None:
            self._owned_client = httpx.AsyncClient(
                base_url=self.base_url,
                headers={"X-Session-API-Key": self._session_key, "Accept": "application/json"},
                timeout=httpx.Timeout(self.timeout_seconds, connect=min(self.timeout_seconds, 10.0)),
                follow_redirects=False,
            )
        return self._owned_client

    async def _json(self, method: str, path: str, **kwargs: Any) -> Any:
        client = await self._get_client()
        response = await client.request(method, path, **kwargs)
        response.raise_for_status()
        if not response.content:
            return {}
        return response.json()

    async def health(self) -> WorkerHealth:
        try:
            data = await self._json("GET", self.verification.endpoint("health"))
            return WorkerHealth(
                worker_id=self.worker_id,
                healthy=True,
                status=str(data.get("status", "healthy")) if isinstance(data, dict) else "healthy",
                runtime_version=self.runtime_version,
                adapter_version=self.adapter_api_version,
                details={"auth": "session_api_key", "report_id": self.verification.report_id},
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

    async def _server_info(self) -> dict[str, Any]:
        data = await self._json("GET", self.verification.endpoint("server_info"))
        return data if isinstance(data, dict) else {}

    async def readiness(self, request: WorkerRunRequest | None = None) -> WorkerReadiness:
        local = await BaseWorkerAdapter.readiness(self, request)
        blockers = list(local.blockers)
        checks: dict[str, bool] = {"adapter_open": not self._closed}
        if not self.context.workspace_path or not Path(self.context.workspace_path).is_absolute():
            blockers.append("AIAT must provide an absolute isolated workspace path")
            checks["workspace_bound"] = False
        else:
            checks["workspace_bound"] = True
        profile_id = self.context.metadata.get("openhands_agent_profile_id")
        try:
            UUID(str(profile_id))
            checks["agent_profile_bound"] = True
        except (TypeError, ValueError):
            checks["agent_profile_bound"] = False
            blockers.append("an operator-provisioned OpenHands agent_profile_id is required")
        if not self.context.metadata.get("openhands_mcp_profile_ref"):
            checks["aiat_tool_bridge_bound"] = False
            blockers.append("the OpenHands profile must reference the approved AIAT MCP bridge")
        else:
            checks["aiat_tool_bridge_bound"] = True
        configured_image_digest = str(self.context.metadata.get("openhands_image_digest") or "")
        checks["image_digest_bound"] = configured_image_digest == self.verification.image_digest
        if not checks["image_digest_bound"]:
            blockers.append("the deployed Agent Server image digest does not match the pinned candidate")
        try:
            health = await self.health()
            checks["authenticated_health"] = health.healthy
            if not health.healthy:
                blockers.append("OpenHands Agent Server health check failed")
            ready = await self._json("GET", self.verification.endpoint("readiness"))
            remote_ready = isinstance(ready, dict) and str(ready.get("status", "ready")).lower() in {"ready", "healthy"}
            checks["server_ready"] = remote_ready
            if not remote_ready:
                blockers.append("OpenHands Agent Server is not ready")
            info = await self._server_info()
            versions = info.get("versions") or info.get("packages") or {}
            if isinstance(versions, dict):
                server_version = versions.get("openhands-agent-server") or versions.get("agent_server")
                checks["server_version_pinned"] = (
                    not server_version
                    or str(server_version).removeprefix("v")
                    == self.verification.release.removeprefix("v")
                )
                if not checks["server_version_pinned"]:
                    blockers.append("Agent Server package version does not match the pinned release")
            else:
                checks["server_version_pinned"] = False
                blockers.append("Agent Server server_info omitted package versions")
            build_sha = info.get("build_sha") or info.get("git_sha") or info.get("commit_sha")
            checks["build_pinned"] = bool(build_sha) and str(build_sha) == self.verification.commit_sha
            if not checks["build_pinned"]:
                blockers.append("Agent Server server_info omitted or mismatched the pinned source commit")
        except Exception as exc:
            checks.update({"authenticated_health": False, "server_ready": False, "server_version_pinned": False, "build_pinned": False})
            blockers.append(f"OpenHands readiness failed: {type(exc).__name__}")
        if request is not None:
            model = request.resolved_model_profile.exact_model_id if request.resolved_model_profile else None
            if not model:
                blockers.append("OpenHands requires an AIAT-resolved exact model ID")
                checks["exact_model_bound"] = False
            else:
                checks["exact_model_bound"] = True
        return WorkerReadiness(worker_id=self.worker_id, ready=not blockers, checks=checks, blockers=blockers)

    def _workspace_path(self) -> Path:
        if not self.context.workspace_path:
            raise RuntimeError("AIAT workspace is not bound")
        path = Path(self.context.workspace_path).resolve()
        if not path.is_absolute():
            raise RuntimeError("AIAT workspace must be absolute")
        return path

    @staticmethod
    def _prompt(request: WorkerRunRequest) -> str:
        value = request.task_input.get("prompt") or request.task_input.get("instruction")
        if not isinstance(value, str) or not value.strip():
            value = f"Complete the AIAT task of type {request.task_type}."
        return value.strip()

    def _start_payload(self, request: WorkerRunRequest) -> dict[str, Any]:
        profile_id = self.context.metadata.get("openhands_agent_profile_id")
        try:
            UUID(str(profile_id))
        except (TypeError, ValueError) as exc:
            raise ValueError("OpenHands agent_profile_id must be a UUID") from exc
        workspace = self._workspace_path()
        max_iterations = int(request.budget.get("max_iterations", request.extensions.get("max_iterations", 500)))
        if max_iterations < 1:
            raise ValueError("OpenHands max_iterations must be positive")
        return {
            "agent_profile_id": str(profile_id),
            "workspace": {"kind": "LocalWorkspace", "working_dir": str(workspace)},
            "worktree": False,
            "initial_message": {
                "role": "user",
                "content": [{"type": "text", "text": self._prompt(request)}],
                "run": False,
            },
            "max_iterations": max_iterations,
            "stuck_detection": True,
            "tags": {
                "aiat_worker_id": self.worker_id,
                "aiat_run_id": str(request.run_id),
                "aiat_idempotency_key": request.idempotency_key[:128],
            },
        }

    async def _conversation(self, conversation_id: str) -> dict[str, Any]:
        data = await self._json("GET", self.verification.endpoint("conversation_get", conversation_id=conversation_id))
        return data if isinstance(data, dict) else {}

    async def _create_conversation(self, request: WorkerRunRequest) -> str:
        existing = self._conversation_by_key.get(request.idempotency_key)
        if existing:
            self._conversation_by_run[request.run_id] = existing
            return existing
        payload = self._start_payload(request)
        data = await self._json("POST", self.verification.endpoint("conversation_create"), json=payload)
        conversation_id = str(data.get("id") or "") if isinstance(data, dict) else ""
        try:
            UUID(conversation_id)
        except ValueError as exc:
            raise RuntimeError("OpenHands conversation creation returned no valid ID") from exc
        info = await self._conversation(conversation_id)
        agent = info.get("agent") if isinstance(info, dict) else None
        llm = agent.get("llm") if isinstance(agent, dict) else None
        expected = request.resolved_model_profile.exact_model_id if request.resolved_model_profile else None
        actual = llm.get("model") if isinstance(llm, dict) else None
        if expected and actual and str(actual) != expected:
            raise RuntimeError("OpenHands agent profile resolved a model different from AIAT's model snapshot")
        self._conversation_by_key[request.idempotency_key] = conversation_id
        self._conversation_by_run[request.run_id] = conversation_id
        return conversation_id

    async def _emit_runtime_event(self, request: WorkerRunRequest, raw: Any) -> None:
        if not isinstance(raw, dict):
            return
        kind = str(raw.get("kind") or raw.get("type") or "openhands.event")
        event_id = raw.get("id") or raw.get("event_id")
        # Only scalar identifiers/status are retained in AIAT evidence. Event
        # payloads can contain prompts, tool arguments, or file contents.
        extensions = {
            "namespace": "openhands",
            "event_kind": kind[:128],
            "runtime_event_id": str(event_id)[:128] if event_id else None,
        }
        await self.emit_progress(request.run_id, f"OpenHands event: {kind}", phase="runtime")
        # Preserve only bounded scalar metadata on the normalized event.
        await self.emit_audit(request.run_id, "openhands.event", details=extensions)

    async def _consume_events(self, request: WorkerRunRequest, conversation_id: str) -> None:
        try:
            import websockets
        except ImportError:
            await self.emit_audit(request.run_id, "openhands.websocket_unavailable", details={"outcome": "blocked"})
            return
        parsed = urlsplit(self.base_url)
        scheme = "wss" if parsed.scheme == "https" else "ws"
        socket_path = self.verification.endpoint("events_socket", conversation_id=conversation_id)
        ws_url = f"{scheme}://{parsed.netloc}{socket_path}"
        try:
            async with websockets.connect(ws_url) as socket:
                await socket.send(json.dumps({"type": "auth", "session_api_key": self._session_key}))
                async for message in socket:
                    if request.run_id in self._stop_events:
                        break
                    try:
                        raw = json.loads(message)
                    except (TypeError, json.JSONDecodeError):
                        continue
                    await self._emit_runtime_event(request, raw)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            if request.run_id not in self._stop_events:
                await self.emit_audit(request.run_id, "openhands.websocket_error", details={"error": type(exc).__name__})

    async def _final_response(self, conversation_id: str) -> str:
        data = await self._json("GET", self.verification.endpoint("agent_final_response", conversation_id=conversation_id))
        return str(data.get("response") or "") if isinstance(data, dict) else ""

    @staticmethod
    def _usage(info: dict[str, Any], request: WorkerRunRequest, duration_ms: float) -> WorkerUsage:
        metrics = info.get("metrics") if isinstance(info.get("metrics"), dict) else {}
        token_usage = metrics.get("accumulated_token_usage") or metrics.get("token_usage") or {}
        if not isinstance(token_usage, dict):
            token_usage = {}
        prompt = int(token_usage.get("prompt_tokens") or token_usage.get("input") or 0)
        completion = int(token_usage.get("completion_tokens") or token_usage.get("output") or 0)
        cost = float(metrics.get("accumulated_cost") or metrics.get("cost_usd") or 0)
        model = request.resolved_model_profile.exact_model_id if request.resolved_model_profile else None
        return WorkerUsage(
            prompt_tokens=max(prompt, 0),
            completion_tokens=max(completion, 0),
            total_tokens=max(prompt + completion, 0),
            cost_usd=max(cost, 0),
            duration_ms=max(duration_ms, 0),
            provider=model.split("/", 1)[0] if model and "/" in model else None,
            exact_model_id=model,
        )

    async def _download_digest(self, absolute_path: Path) -> tuple[str, int]:
        client = await self._get_client()
        digest = hashlib.sha256()
        size = 0
        async with client.stream("GET", self.verification.endpoint("file_download"), params={"path": str(absolute_path)}) as response:
            if response.status_code == 404:
                raise FileNotFoundError(str(absolute_path))
            response.raise_for_status()
            async for chunk in response.aiter_bytes():
                digest.update(chunk)
                size += len(chunk)
        return digest.hexdigest(), size

    async def _artifacts(self, conversation_id: str) -> list[WorkerArtifact]:
        root = self._workspace_path()
        client = await self._get_client()
        response = await client.get(self.verification.endpoint("git_changes"), params={"path": str(root), "ref": "HEAD"})
        response.raise_for_status()
        raw_changes = response.json() if response.content else []
        artifacts: list[WorkerArtifact] = []
        if not isinstance(raw_changes, list):
            return artifacts
        for item in raw_changes:
            if not isinstance(item, dict) or str(item.get("status", "")).upper() == "DELETED":
                continue
            rel = str(item.get("path") or "")
            if not rel:
                continue
            candidate = (root / PurePosixPath(rel)).resolve()
            if candidate != root and not candidate.is_relative_to(root):
                continue
            try:
                digest, size = await self._download_digest(candidate)
            except FileNotFoundError:
                continue
            artifact = WorkerArtifact(
                kind=ArtifactKind.FILE,
                name=rel,
                uri=rel,
                sha256=digest,
                size_bytes=size,
                metadata={"openhands_conversation_id": conversation_id},
            )
            artifacts.append(artifact)
            if self.context.artifact_registrar is not None:
                await self.context.artifact_registrar(artifact)
        return artifacts

    async def _execute(self, request: WorkerRunRequest) -> WorkerResult:
        conversation_id = self._conversation_by_run.get(request.run_id) or await self._create_conversation(request)
        self._stop_events.discard(request.run_id)
        event_task = asyncio.create_task(self._consume_events(request, conversation_id), name=f"openhands-events-{request.run_id}")
        self._event_tasks[request.run_id] = event_task
        started = time.monotonic()
        await self._json("POST", self.verification.endpoint("conversation_run", conversation_id=conversation_id))
        try:
            while True:
                if request.run_id in self._cancelled or request.run_id in self._cancel_requested:
                    return WorkerResult(
                        run_id=request.run_id,
                        worker_id=self.worker_id,
                        success=False,
                        error=WorkerError(code="CANCELLED", message="OpenHands conversation interrupted by AIAT", terminal=True, category="cancellation"),
                        replay_metadata={"openhands_conversation_id": conversation_id},
                    )
                info = await self._conversation(conversation_id)
                status = str(info.get("execution_status") or "").lower()
                if status in TERMINAL_STATUSES:
                    if status != "finished":
                        return WorkerResult(
                            run_id=request.run_id,
                            worker_id=self.worker_id,
                            success=False,
                            error=WorkerError(code="OPENHANDS_CONVERSATION_ERROR", message=f"OpenHands conversation ended in {status}", retryable=status == "error", category="runtime"),
                            replay_metadata={"openhands_conversation_id": conversation_id, "execution_status": status},
                        )
                    return WorkerResult(
                        run_id=request.run_id,
                        worker_id=self.worker_id,
                        success=True,
                        output=await self._final_response(conversation_id),
                        artifacts=await self._artifacts(conversation_id),
                        usage=self._usage(info, request, (time.monotonic() - started) * 1000),
                        replay_metadata={
                            "openhands_conversation_id": conversation_id,
                            "openhands_release": self.verification.release,
                            "openhands_commit_sha": self.verification.commit_sha,
                            "image_digest": self.verification.image_digest,
                            "execution_status": status,
                        },
                    )
                if request.timeout_seconds and time.monotonic() - started > request.timeout_seconds:
                    raise TimeoutError("OpenHands conversation exceeded the AIAT adapter timeout")
                await asyncio.sleep(0.25)
        finally:
            self._stop_events.add(request.run_id)
            task = self._event_tasks.pop(request.run_id, None)
            if task is not None:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            await self._cleanup_conversation(conversation_id)

    async def _cleanup_conversation(self, conversation_id: str) -> None:
        if not self.context.metadata.get("openhands_cleanup_conversations"):
            return
        client = await self._get_client()
        response = await client.delete(self.verification.endpoint("conversation_delete", conversation_id=conversation_id))
        if response.status_code not in {200, 404}:
            response.raise_for_status()

    async def pause(self, request: WorkerPause) -> None:
        conversation_id = self._conversation_by_run.get(request.run_id)
        if conversation_id:
            await self._json("POST", self.verification.endpoint("conversation_pause", conversation_id=conversation_id))
        await super().pause(request)

    async def resume(self, request: WorkerResume) -> None:
        conversation_id = self._conversation_by_run.get(request.run_id)
        if not conversation_id:
            raise RuntimeError("OpenHands resume requires a known conversation ID")
        await self._json("POST", self.verification.endpoint("conversation_run", conversation_id=conversation_id))
        await super().resume(request)

    async def cancel(self, request: WorkerCancellation) -> None:
        conversation_id = self._conversation_by_run.get(request.run_id)
        if conversation_id:
            endpoint = "conversation_interrupt" if request.force else "conversation_pause"
            response = await (await self._get_client()).post(self.verification.endpoint(endpoint, conversation_id=conversation_id))
            if response.status_code >= 400 and not request.force:
                response.raise_for_status()
        self._cancelled.add(request.run_id)
        await super().cancel(request)

    async def close(self) -> None:
        self._stop_events.update(self._event_tasks)
        await super().close()
        if self._owned_client is not None:
            await self._owned_client.aclose()
            self._owned_client = None

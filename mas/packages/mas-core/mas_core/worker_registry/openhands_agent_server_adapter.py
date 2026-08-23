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
import re
import time
from dataclasses import dataclass, field
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
from mas_core.worker_contract.openhands_bridge import issue_openhands_tool_grant

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
    "settings_mcp": "/api/settings/mcp/{settings_key}",
}

OPENHANDS_MCP_BRIDGE_URL = "http://tool-service:8002/openhands/mcp"
_OPENHANDS_MODEL_ID = "omniroute-coding"
_OPENHANDS_MCP_SERVER_KEY_PREFIX = "aiat-openhands-"
_OPENHANDS_MCP_GRANT_TTL_SECONDS = 300
_OPENHANDS_MAX_ITERATIONS = 20
_OPENHANDS_TIMEOUT_SECONDS = 300
# Keep the candidate's AIAT bridge surface bounded even when a caller builds a
# WorkerRunRequest directly.  The profile/preflight carries the same contract,
# but the adapter must not mint a broader signed grant from untrusted input.
_OPENHANDS_ALLOWED_TOOL_GRANTS = frozenset(
    {
        "aiat.repository.read",
        "aiat.repository.write",
        "aiat.tests.execute",
    }
)

TERMINAL_STATUSES = frozenset({"finished", "error", "stuck"})

# This sentinel is intentionally module-private.  A metadata flag, request
# extension, or caller-selected boolean is not sufficient to enter the
# certification path; the adapter requires a token issued by the dedicated
# AIAT certification controller and bound to the exact candidate pins.
_CERTIFICATION_AUTHORITY = object()
_CERTIFICATION_FACTORY_TOKEN = object()
_CONSUMED_CERTIFICATION_AUTHORIZATIONS: set[object] = set()
_CERTIFICATION_AUTHORIZATION_TTL_SECONDS = 900
_CERTIFICATION_CONTROLLER = "aiat-github-actions"
_CERTIFICATION_SANDBOX_PROFILE = "gvisor"
_CERTIFICATION_SANDBOX_RUNTIME = "runsc"
_CERTIFICATION_WORKER_ID = "coding-worker-openhands-candidate"


@dataclass(frozen=True, slots=True)
class OpenHandsCertificationAuthorization:
    """Opaque authorization for one isolated candidate certification run.

    This is deliberately not an activation approval.  It is scoped to one
    controller run, candidate commit/image digest, and native gVisor runtime.
    The private authority marker prevents ordinary runtime metadata from
    spoofing certification mode.
    """

    candidate_commit: str
    image_digest: str
    sandbox_profile: str
    sandbox_runtime: str
    controller: str
    controller_run_id: str
    worker_id: str = _CERTIFICATION_WORKER_ID
    _authority: object = field(default=None, repr=False, compare=False)
    issued_at: float = 0.0
    expires_at: float = 0.0

    @property
    def scope(self) -> str:
        return "CERTIFICATION_AUTHORIZATION"


def issue_openhands_certification_authorization(
    verification: OpenHandsInterfaceVerification,
    *,
    controller: str,
    controller_run_id: str,
    sandbox_profile: str,
    sandbox_runtime: str,
    candidate_identity: str = _CERTIFICATION_WORKER_ID,
) -> OpenHandsCertificationAuthorization:
    """Issue a run-scoped authorization for the trusted certification controller.

    The caller must be the AIAT certification controller.  This helper only
    issues a narrowly scoped token; it never changes the report's ``approved``
    flag and never creates an activation approval.
    """

    issuer = str(controller or "").strip()
    run_id = str(controller_run_id or "").strip()
    profile = str(sandbox_profile or "").strip().lower()
    runtime = str(sandbox_runtime or "").strip().lower()
    if issuer != _CERTIFICATION_CONTROLLER:
        raise ValueError("OpenHands certification authorization requires the trusted controller")
    if not re.fullmatch(r"[0-9]{1,32}", run_id):
        raise ValueError("certification controller run ID must be a bounded numeric GitHub run ID")
    if not re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,127}", candidate_identity):
        raise ValueError("OpenHands certification requires a bounded candidate worker identity")
    if profile != _CERTIFICATION_SANDBOX_PROFILE:
        raise ValueError("OpenHands certification requires the gVisor sandbox profile")
    if runtime != _CERTIFICATION_SANDBOX_RUNTIME:
        raise ValueError("OpenHands certification requires the runsc sandbox runtime")
    if not re.fullmatch(r"[0-9a-fA-F]{40}", verification.commit_sha):
        raise ValueError("OpenHands certification requires a full pinned candidate commit")
    if not verification.image_digest.startswith("sha256:"):
        raise ValueError("OpenHands certification requires a pinned OCI image digest")
    return OpenHandsCertificationAuthorization(
        candidate_commit=verification.commit_sha,
        image_digest=verification.image_digest,
        sandbox_profile=profile,
        sandbox_runtime=runtime,
        controller=issuer,
        controller_run_id=run_id,
        worker_id=candidate_identity,
        _authority=_CERTIFICATION_AUTHORITY,
        issued_at=time.time(),
        expires_at=time.time() + _CERTIFICATION_AUTHORIZATION_TTL_SECONDS,
    )


def _valid_certification_authorization(
    authorization: OpenHandsCertificationAuthorization | None,
    verification: OpenHandsInterfaceVerification,
    *,
    expected_worker_id: str | None = None,
) -> bool:
    return bool(
        isinstance(authorization, OpenHandsCertificationAuthorization)
        and authorization._authority is _CERTIFICATION_AUTHORITY
        and authorization.controller_run_id
        and authorization.candidate_commit == verification.commit_sha
        and authorization.image_digest == verification.image_digest
        and authorization.sandbox_profile == _CERTIFICATION_SANDBOX_PROFILE
        and authorization.sandbox_runtime == _CERTIFICATION_SANDBOX_RUNTIME
        and authorization.controller == _CERTIFICATION_CONTROLLER
        and (expected_worker_id is None or authorization.worker_id == expected_worker_id)
        and authorization.issued_at > 0
        and authorization.expires_at > authorization.issued_at
        and authorization.issued_at <= time.time() < authorization.expires_at
    )


@dataclass(frozen=True, slots=True)
class OpenHandsInterfaceVerification:
    """Pinned interface evidence selected by an operator/steward.

    ``approved`` is intentionally false for the committed candidate report.
    The ordinary constructor requires an approved report for activation, while
    ``for_certification`` requires a separate run-scoped certification
    authorization.  A version pin by itself is never activation evidence.
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
        approval_record_id = str(payload["approval_record_id"]) if payload.get("approval_record_id") else None
        approved = bool(payload.get("approved")) and approval_status == "APPROVED"
        if approved and not approval_record_id:
            raise ValueError("approved OpenHands report must include an approval record ID")
        return cls(
            report_id=str(payload.get("report_id") or "openhands-interface-report"),
            release=release,
            commit_sha=commit_sha,
            repository=repository,
            image_ref=image_ref,
            image_digest=image_digest,
            image_platform_digest=str(platform_digest) if platform_digest else None,
            endpoints=endpoints,
            approved=approved,
            approval_record_id=approval_record_id,
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

    The server-side agent profile must already contain the governed model and
    AIAT MCP bridge.  Task input can provide a prompt only; it cannot select a
    workspace, agent profile, model, credentials, or external tools.  Normal
    construction is activation-scoped and requires approved interface
    evidence; the explicit ``for_certification`` factory is the only pending
    report path.
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
        certification_authorization: OpenHandsCertificationAuthorization | None = None,
        _certification_factory_token: object | None = None,
    ) -> None:
        context = context or AdapterContext()
        metadata_certification_mode = context.metadata.get("openhands_certification_mode") is True
        certification_mode = certification_authorization is not None
        if certification_mode and _certification_factory_token is not _CERTIFICATION_FACTORY_TOKEN:
            raise ValueError("OpenHands certification mode is available only through the trusted certification factory")
        if certification_mode and not _valid_certification_authorization(
            certification_authorization,
            verification,
            expected_worker_id=worker_id,
        ):
            raise ValueError("OpenHands certification authorization is invalid or does not match the pinned candidate")
        if certification_mode:
            controller = str(context.metadata.get("openhands_certification_controller") or "")
            controller_run_id = str(context.metadata.get("openhands_certification_controller_run_id") or "")
            if (
                controller != certification_authorization.controller
                or controller_run_id != certification_authorization.controller_run_id
            ):
                raise ValueError("OpenHands certification controller attestation does not match the authorization")
        if metadata_certification_mode and not certification_mode:
            raise ValueError("OpenHands certification mode requires AIAT certification authorization")
        if not verification.approved and not certification_mode:
            raise ValueError("OpenHands adapter requires an approved interface verification report")
        if not base_url or not urlsplit(base_url).scheme:
            raise ValueError("OpenHands Agent Server base URL is required")
        session_key = str(context.secrets.get("openhands_session_api_key") or "")
        if not session_key:
            raise ValueError("OpenHands requires a session API key from the AIAT secret boundary")
        if certification_mode:
            context.metadata.update(
                {
                    "openhands_certification_mode": True,
                    "openhands_certification_controller_run_id": certification_authorization.controller_run_id,
                    "openhands_certification_sandbox_profile": certification_authorization.sandbox_profile,
                    "openhands_certification_sandbox_runtime": certification_authorization.sandbox_runtime,
                    "openhands_activation_eligible": False,
                }
            )
        super().__init__(
            worker_id=worker_id,
            capabilities=_capabilities(),
            context=context,
            runtime_version=verification.release,
        )
        self.verification = verification
        self._certification_authorization = certification_authorization
        self._certification_mode = certification_mode
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
        self._mcp_by_run: dict[UUID, str] = {}
        self._mcp_grant_expires_at: dict[UUID, float] = {}

    @classmethod
    def for_certification(
        cls,
        verification: OpenHandsInterfaceVerification,
        *,
        authorization: OpenHandsCertificationAuthorization,
        base_url: str,
        worker_id: str,
        client: httpx.AsyncClient | None = None,
        context: AdapterContext | None = None,
        timeout_seconds: float = 60.0,
    ) -> OpenHandsAgentServerAdapter:
        """Construct the isolated certification-only adapter.

        A certification authorization can execute the pinned candidate in the
        disposable gVisor workflow, but it cannot make the adapter eligible for
        normal worker activation.  Production callers must use the ordinary
        constructor, which continues to require a steward-approved report.
        """

        if authorization in _CONSUMED_CERTIFICATION_AUTHORIZATIONS:
            raise ValueError("OpenHands certification authorization has already been used")
        if not _valid_certification_authorization(authorization, verification, expected_worker_id=worker_id):
            raise ValueError("OpenHands certification authorization is invalid or does not match the pinned candidate")
        _CONSUMED_CERTIFICATION_AUTHORIZATIONS.add(authorization)
        return cls(
            verification,
            base_url=base_url,
            worker_id=worker_id,
            client=client,
            context=context,
            timeout_seconds=timeout_seconds,
            certification_authorization=authorization,
            _certification_factory_token=_CERTIFICATION_FACTORY_TOKEN,
        )

    @property
    def certification_mode(self) -> bool:
        """Whether this adapter is restricted to one certification run."""

        return self._certification_mode

    @property
    def activation_eligible(self) -> bool:
        """Whether this instance may be used by the normal activation path."""

        return not self._certification_mode

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
            blockers.append("an AIAT-governed OpenHands agent_profile_id is required (run-scoped during certification)")
        if self._certification_mode:
            checks["certification_authorized"] = True
            checks["certification_gvisor_policy"] = (
                self.context.metadata.get("openhands_certification_sandbox_profile") == _CERTIFICATION_SANDBOX_PROFILE
                and self.context.metadata.get("openhands_certification_sandbox_runtime") == _CERTIFICATION_SANDBOX_RUNTIME
            )
            if not checks["certification_gvisor_policy"]:
                blockers.append("OpenHands certification requires the governed gVisor/runsc policy")
        if not self.context.metadata.get("openhands_mcp_profile_ref"):
            checks["aiat_tool_bridge_bound"] = False
            blockers.append("the OpenHands profile must reference the approved AIAT MCP bridge")
        else:
            checks["aiat_tool_bridge_bound"] = True
        bridge_url = str(self.context.metadata.get("openhands_mcp_bridge_url") or "")
        checks["aiat_tool_bridge_url_pinned"] = bridge_url == OPENHANDS_MCP_BRIDGE_URL
        if not checks["aiat_tool_bridge_url_pinned"]:
            blockers.append("OpenHands must use the fixed internal AIAT MCP bridge URL")
        settings_key = str(self.context.metadata.get("openhands_mcp_settings_key") or "")
        checks["aiat_tool_bridge_settings_key_pinned"] = settings_key.startswith(_OPENHANDS_MCP_SERVER_KEY_PREFIX)
        if not checks["aiat_tool_bridge_settings_key_pinned"]:
            blockers.append("OpenHands requires a disposable aiat-openhands-* MCP settings key")
        checks["aiat_tool_bridge_signing_secret"] = bool(self.context.secrets.get("tool_secret"))
        if not checks["aiat_tool_bridge_signing_secret"]:
            blockers.append("OpenHands requires an AIAT bridge signing secret from the secret boundary")
        configured_model = str(self.context.metadata.get("openhands_model_id") or "")
        checks["governed_model_id_pinned"] = configured_model == _OPENHANDS_MODEL_ID
        if not checks["governed_model_id_pinned"]:
            blockers.append("OpenHands must use the governed omniroute-coding model ID")
        for metadata_key in (
            "openhands_public_skills_disabled",
            "openhands_plugins_disabled",
            "openhands_subagents_disabled",
            "openhands_browser_disabled",
            "openhands_direct_credentials_disabled",
        ):
            checks[metadata_key] = self.context.metadata.get(metadata_key) is True
            if not checks[metadata_key]:
                blockers.append(f"{metadata_key} must be explicitly true in the governed profile")
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
            checks["exact_model_bound"] = model == configured_model == _OPENHANDS_MODEL_ID
            if not model:
                blockers.append("OpenHands requires an AIAT-resolved exact model ID")
            elif model != configured_model or model != _OPENHANDS_MODEL_ID:
                blockers.append("OpenHands request model does not match the governed model snapshot")
        return WorkerReadiness(worker_id=self.worker_id, ready=not blockers, checks=checks, blockers=blockers)

    def _mcp_settings_key(self) -> str:
        value = str(self.context.metadata.get("openhands_mcp_settings_key") or "")
        if not value.startswith(_OPENHANDS_MCP_SERVER_KEY_PREFIX):
            raise RuntimeError("OpenHands MCP settings key is not a disposable AIAT key")
        return value

    async def _configure_tool_bridge(self, request: WorkerRunRequest) -> None:
        """Create one fixed, run-scoped remote MCP entry in Agent Server settings.

        The dedicated settings key must be absent before the run.  A conflict
        is a hard failure rather than an overwrite: this prevents a candidate
        run from clobbering an operator's other MCP configuration.  The key is
        deleted in the execution cleanup path.
        """

        if request.run_id in self._mcp_by_run:
            return
        requested_tools = frozenset(str(name).strip() for name in request.tool_grants if str(name).strip())
        unexpected_tools = requested_tools - _OPENHANDS_ALLOWED_TOOL_GRANTS
        if unexpected_tools:
            raise RuntimeError(
                "OpenHands tool grants exceed the bounded coding surface: "
                + ", ".join(sorted(unexpected_tools))
            )
        settings_key = self._mcp_settings_key()
        if self.context.metadata.get("openhands_mcp_preconfigured") is True:
            # The workflow provisioning step owns creation of the entry when
            # the Agent Server is disposable.  The adapter must only consume
            # a redacted readback here; posting again would either overwrite
            # the run grant or fail with a conflict.
            settings = await self._json("GET", "/api/settings")
            config = settings.get("mcp_config") if isinstance(settings, dict) else None
            if not isinstance(config, dict):
                config = settings.get("mcp_servers") if isinstance(settings, dict) else None
            entry = config.get(settings_key) if isinstance(config, dict) else None
            if not isinstance(entry, dict):
                raise RuntimeError("preconfigured OpenHands MCP settings entry is absent")
            if entry.get("url") != OPENHANDS_MCP_BRIDGE_URL:
                raise RuntimeError("preconfigured OpenHands MCP settings URL is not the fixed AIAT bridge")
            if entry.get("transport") != "streamable-http" or entry.get("enabled") is not True:
                raise RuntimeError("preconfigured OpenHands MCP settings transport or enabled state is invalid")
            headers = entry.get("headers")
            if not isinstance(headers, dict) or "X-AIAT-OpenHands-Grant" not in headers:
                raise RuntimeError("preconfigured OpenHands MCP settings does not contain the AIAT grant header")
            self._mcp_by_run[request.run_id] = settings_key
            self._mcp_grant_expires_at[request.run_id] = float(time.time() + _OPENHANDS_MCP_GRANT_TTL_SECONDS)
            await self.emit_audit(
                request.run_id,
                "openhands.mcp_bridge_consumed",
                details={"settings_key": settings_key, "bridge": "aiat.openhands.mcp.v1", "run_scoped": True},
            )
            return
        issued_at = int(time.time())
        signing_secret = str(self.context.secrets.get("tool_secret") or "")
        grant = issue_openhands_tool_grant(
            signing_secret,
            worker_id=self.worker_id,
            run_id=request.run_id,
            project_id=request.project_id,
            tool_names=requested_tools,
            ttl_seconds=_OPENHANDS_MCP_GRANT_TTL_SECONDS,
            now=issued_at,
        )
        response = await (await self._get_client()).post(
            self.verification.endpoint("settings_mcp", settings_key=settings_key),
            json={
                "url": OPENHANDS_MCP_BRIDGE_URL,
                "transport": "streamable-http",
                "headers": {"X-AIAT-OpenHands-Grant": grant},
                "enabled": True,
                "timeout": 60.0,
            },
        )
        if response.status_code == 409:
            raise RuntimeError("OpenHands MCP settings key already exists; refusing to overwrite it")
        response.raise_for_status()
        self._mcp_by_run[request.run_id] = settings_key
        self._mcp_grant_expires_at[request.run_id] = float(issued_at + _OPENHANDS_MCP_GRANT_TTL_SECONDS)
        await self.emit_audit(
            request.run_id,
            "openhands.mcp_bridge_configured",
            details={"settings_key": settings_key, "bridge": "aiat.openhands.mcp.v1"},
        )

    async def _cleanup_tool_bridge(self, run_id: UUID) -> None:
        settings_key = self._mcp_by_run.pop(run_id, None)
        self._mcp_grant_expires_at.pop(run_id, None)
        if settings_key is None:
            return
        if self.certification_mode and self.context.metadata.get("openhands_defer_mcp_cleanup") is True:
            # A governed lifecycle wave may run several conversations against
            # one profile-bound MCP registration.  The trusted certification
            # controller performs one final delete/read-back after the wave;
            # ordinary adapter runs retain the existing per-run cleanup.
            self._mcp_by_run[run_id] = settings_key
            return
        response = await (await self._get_client()).delete(
            self.verification.endpoint("settings_mcp", settings_key=settings_key)
        )
        # Treat a successful empty delete (204) the same as an explicit 200
        # or an already-absent 404. The Agent Server API is allowed to use
        # either success shape for this idempotent run-scoped cleanup.
        if response.status_code not in {200, 204, 404}:
            response.raise_for_status()
        await self.emit_audit(
            run_id,
            "openhands.mcp_bridge_cleaned",
            details={
                "settings_key": settings_key,
                "outcome": "deleted" if response.status_code in {200, 204} else "already_absent",
            },
        )

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
        # ``extensions`` are task metadata and never a budget authority.  The
        # AIAT controller supplies the governed budget snapshot explicitly;
        # absent that snapshot, use the bounded adapter default.
        max_iterations = int(request.budget.get("max_iterations", _OPENHANDS_MAX_ITERATIONS))
        if max_iterations < 1:
            raise ValueError("OpenHands max_iterations must be positive")
        if max_iterations > _OPENHANDS_MAX_ITERATIONS:
            raise ValueError("OpenHands max_iterations exceeds the governed candidate budget")
        if request.timeout_seconds and request.timeout_seconds > _OPENHANDS_TIMEOUT_SECONDS:
            raise ValueError("OpenHands timeout exceeds the governed candidate budget")
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
        await self._configure_tool_bridge(request)
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
        expected = str(self.context.metadata.get("openhands_model_id") or _OPENHANDS_MODEL_ID)
        actual = llm.get("model") if isinstance(llm, dict) else None
        if not actual or str(actual) != expected:
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
        base_path = parsed.path.rstrip("/")
        ws_url = f"{scheme}://{parsed.netloc}{base_path}{socket_path}"
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
        conversation_id: str | None = None
        try:
            conversation_id = self._conversation_by_run.get(request.run_id) or await self._create_conversation(request)
            self._stop_events.discard(request.run_id)
            event_task = asyncio.create_task(self._consume_events(request, conversation_id), name=f"openhands-events-{request.run_id}")
            self._event_tasks[request.run_id] = event_task
            started = time.monotonic()
            try:
                await self._json("POST", self.verification.endpoint("conversation_run", conversation_id=conversation_id))
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
                        await self._interrupt_for_timeout(request, conversation_id)
                        return WorkerResult(
                            run_id=request.run_id,
                            worker_id=self.worker_id,
                            success=False,
                            error=WorkerError(
                                code="TIMEOUT",
                                message="OpenHands conversation exceeded the AIAT adapter timeout",
                                terminal=True,
                                category="timeout",
                            ),
                            replay_metadata={"openhands_conversation_id": conversation_id},
                        )
                    await asyncio.sleep(0.25)
            finally:
                self._stop_events.add(request.run_id)
                task = self._event_tasks.pop(request.run_id, None)
                if task is not None:
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
                await self._cleanup_conversation(conversation_id)
        finally:
            await self._cleanup_tool_bridge(request.run_id)

    async def _cleanup_conversation(self, conversation_id: str) -> None:
        if not self.context.metadata.get("openhands_cleanup_conversations"):
            return
        client = await self._get_client()
        response = await client.delete(self.verification.endpoint("conversation_delete", conversation_id=conversation_id))
        if response.status_code not in {200, 404}:
            response.raise_for_status()

    async def _interrupt_for_timeout(self, request: WorkerRunRequest, conversation_id: str) -> None:
        """Ask Agent Server to stop before deleting a timed-out conversation.

        AIAT's timeout is authoritative. The remote interrupt is a best-effort
        reconciliation step; the normalized result remains a terminal timeout
        even when the disposable server has already stopped or disappeared.
        """

        try:
            response = await (await self._get_client()).post(
                self.verification.endpoint("conversation_interrupt", conversation_id=conversation_id)
            )
            if response.status_code not in {200, 404, 409}:
                response.raise_for_status()
            await self.emit_audit(
                request.run_id,
                "openhands.timeout_interrupt",
                details={"outcome": "requested", "http_status": response.status_code},
            )
        except Exception as exc:  # cleanup remains authoritative and fail-closed
            await self.emit_audit(
                request.run_id,
                "openhands.timeout_interrupt",
                details={"outcome": "unavailable", "error": type(exc).__name__},
            )

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

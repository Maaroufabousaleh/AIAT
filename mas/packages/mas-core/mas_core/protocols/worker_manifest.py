"""Worker manifest protocol models (`workers/{agent_id}.yaml`)."""

from __future__ import annotations

from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field

from .capability import CapabilityDef

WORKER_SDK_VERSION = "aiat-worker-sdk.v1"

# Epsilon runtime tiers — used in WorkerManifest.runtime_tier
RUNTIME_TIER_LITERAL = Literal[
    "builtin", "langgraph", "crewai", "autogen", "letta",
    "microsoft_agent_framework", "external",
]


class WorkerMetadata(BaseModel):
    id: str
    name: str
    version: str = "1.0"
    description: str | None = None
    source_repo: str | None = None
    source_revision: str | None = None
    version_pin: str | None = None
    update_policy: Literal["manual", "auto-patch", "auto-minor", "auto-all"] = "manual"
    evaluation_status: Literal["pending", "approved", "rejected", "deprecated"] | None = None
    tags: list[str] = Field(default_factory=list)
    migration_status: Literal["native", "compatibility", "migrated", "blocked"] = "native"


class WorkerRuntime(BaseModel):
    transport: Literal["native", "process", "http", "oci", "mcp", "opencode", "aiat_gateway", "human"] = "process"
    adapter_config: dict[str, Any] = Field(default_factory=dict)
    timeout_seconds: int = Field(default=300, ge=1)
    stop_grace_seconds: int = Field(default=60, ge=1)


class WorkerIntegration(BaseModel):
    adapter_entrypoint: str = "WorkerAgent"
    adapter_module: str | None = None
    wrapper_config: dict[str, Any] = Field(default_factory=dict)
    compatibility_tests: list[str] = Field(default_factory=list)
    # native/wrapper/fork: existing AIAT modes
    # langgraph/crewai/autogen/letta/microsoft_agent_framework: advanced runtime modes
    isolation_mode: Literal[
        "native", "wrapper", "fork",
        "langgraph", "crewai", "autogen", "letta", "opencode",
        "microsoft_agent_framework",
    ] = "native"
    contract_version: str = "aiat.worker.v1"
    adapter_api_version: str = "aiat.adapter.v1"
    runtime_api_version: str | None = None
    certified_adapter_version: str | None = None
    steward_id: UUID | None = None
    active_skill_bundle_id: UUID | None = None
    capability_snapshot_version: str | None = None


class WorkerLimits(BaseModel):
    max_concurrent_tasks: int = Field(default=10, ge=1)
    max_instances: int = Field(default=1, ge=1)
    rate_limit_per_minute: int = Field(default=60, ge=1)
    max_payload_size_bytes: int = Field(default=10_485_760, ge=1024)


class WorkerSandbox(BaseModel):
    profile: Literal["standard", "restricted", "gvisor", "firecracker"] = "standard"
    filesystem: dict[str, Any] = Field(default_factory=dict)
    network_mode: Literal["egress-allowlist", "egress-deny-all", "unrestricted"] = (
        "egress-allowlist"
    )
    egress_allowlist: list[str] = Field(default_factory=list)
    linux_security: dict[str, Any] = Field(default_factory=dict)


class WorkerCheckpointing(BaseModel):
    enabled: bool = True
    strategy: Literal["on-step", "periodic", "on-signal", "on_every_llm_call"] = "on_every_llm_call"
    every_n_calls: int = Field(default=1, ge=1)
    store: dict[str, Any] = Field(default_factory=dict)


class WorkerObservability(BaseModel):
    logs_format: Literal["json", "text"] = "json"
    metrics_enabled: bool = True
    traces_enabled: bool = True


class WorkerManifest(BaseModel):
    protocol_version: Literal["aiat.v1"] = Field(
        default="aiat.v1",
        description="Non-breaking protocol version for cross-runtime contract validation.",
    )
    metadata: WorkerMetadata
    runtime: WorkerRuntime = Field(default_factory=WorkerRuntime)
    capabilities: list[CapabilityDef] = Field(default_factory=list)
    integration: WorkerIntegration = Field(default_factory=WorkerIntegration)
    limits: WorkerLimits = Field(default_factory=WorkerLimits)
    sandbox: WorkerSandbox = Field(default_factory=WorkerSandbox)
    checkpointing: WorkerCheckpointing = Field(default_factory=WorkerCheckpointing)
    observability: WorkerObservability = Field(default_factory=WorkerObservability)
    # Epsilon fields — runtime tier and configuration for advanced runtimes
    runtime_tier: RUNTIME_TIER_LITERAL = "builtin"
    runtime_config: dict[str, Any] = Field(default_factory=dict)
    inner_runtime: bool = False
    allowed_inner_runtimes: list[RUNTIME_TIER_LITERAL] = Field(default_factory=list)
    # Universal Specialist Shell metadata. Defaults preserve compatibility with
    # existing YAML while allowing the migration pipeline to mark incomplete
    # records explicitly rather than pretending they are certified.
    shell_version: str = "1.0.0"
    model_mode: Literal["none", "aiat_gateway", "certified_external_runtime", "hybrid"] = "aiat_gateway"
    model_profile_id: str | None = None
    model_requirements: list[str] = Field(default_factory=list)
    permission_grants: list[str] = Field(default_factory=list)
    tool_grants: list[str] = Field(default_factory=list)
    source_provenance: dict[str, Any] = Field(default_factory=dict)
    active_adapter_version: str | None = None
    active_skill_bundle_id: UUID | None = None
    steward_id: UUID | None = None
    certification_status: Literal["pending", "certified", "approved", "blocked", "compatibility"] = "pending"

    @property
    def requires_model_profile(self) -> bool:
        return self.model_mode != "none"

    @property
    def is_legacy_external_wrapper(self) -> bool:
        return self.runtime_tier == "external" and self.integration.isolation_mode == "wrapper" and not self.integration.certified_adapter_version

    def to_specialist_shell(self):
        """Translate a legacy YAML manifest into the universal shell model."""
        from mas_core.worker_contract import (
            ModelMode,
            ProtocolVersion,
            WorkerCapabilities,
            WorkerIdentity,
            WorkerManifest as UniversalWorkerManifest,
        )

        capability_names = [capability.name for capability in self.capabilities]
        return UniversalWorkerManifest(
            protocol=ProtocolVersion(
                contract_version=self.integration.contract_version,
                adapter_api_version=self.integration.adapter_api_version,
                runtime_api_version=self.integration.runtime_api_version,
                capability_snapshot_version=self.integration.capability_snapshot_version,
            ),
            identity=WorkerIdentity(
                worker_id=self.metadata.id,
                shell_version=self.shell_version,
                name=self.metadata.name,
                department=(self.metadata.tags[1] if len(self.metadata.tags) > 1 else None),
                organizational_role=self.metadata.tags[0] if self.metadata.tags else None,
                lifecycle_state="ACTIVE" if self.metadata.evaluation_status == "approved" else "INACTIVE",
                steward_id=self.steward_id or self.integration.steward_id,
                active_adapter_version=self.active_adapter_version or self.integration.certified_adapter_version,
                active_skill_bundle_id=self.active_skill_bundle_id or self.integration.active_skill_bundle_id,
            ),
            capabilities=WorkerCapabilities(
                capability_names=capability_names,
                model_mode=ModelMode(self.model_mode),
                checkpoint_mode="native" if self.checkpointing.enabled else "unsupported",
                workspace_mode="isolated",
                extensions={"legacy_manifest": {"runtime_tier": self.runtime_tier, "isolation_mode": self.integration.isolation_mode}},
            ),
            model_requirements=set(self.model_requirements),
            permissions=list(self.permission_grants),
            tool_grants=list(self.tool_grants),
            sandbox_profile=self.sandbox.profile,
            transport=self.runtime.transport,
            adapter_type=self.integration.isolation_mode,
            source_provenance=self.source_provenance or {
                "repository": self.metadata.source_repo,
                "revision": self.metadata.source_revision,
                "version_pin": self.metadata.version_pin,
            },
            active_adapter_version=self.active_adapter_version or self.integration.certified_adapter_version,
            active_skill_bundle_id=self.active_skill_bundle_id or self.integration.active_skill_bundle_id,
            model_profile={"profile_id": self.model_profile_id} if self.model_profile_id else None,
        )

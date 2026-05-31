"""Worker manifest protocol models (`workers/{agent_id}.yaml`)."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from .capability import CapabilityDef

WORKER_SDK_VERSION = "aiat-worker-sdk.v1"

# Epsilon runtime tiers — used in WorkerManifest.runtime_tier
RUNTIME_TIER_LITERAL = Literal[
    "builtin", "langgraph", "crewai", "autogen", "letta", "external"
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


class WorkerRuntime(BaseModel):
    transport: Literal["process", "http", "oci", "mcp", "human"] = "process"
    adapter_config: dict[str, Any] = Field(default_factory=dict)
    timeout_seconds: int = Field(default=300, ge=1)
    stop_grace_seconds: int = Field(default=60, ge=1)


class WorkerIntegration(BaseModel):
    adapter_entrypoint: str = "WorkerAgent"
    adapter_module: str | None = None
    wrapper_config: dict[str, Any] = Field(default_factory=dict)
    compatibility_tests: list[str] = Field(default_factory=list)
    # native/wrapper/fork: existing AIAT modes
    # langgraph/crewai/autogen/letta: Epsilon advanced runtime modes
    isolation_mode: Literal[
        "native", "wrapper", "fork",
        "langgraph", "crewai", "autogen", "letta",
    ] = "native"


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

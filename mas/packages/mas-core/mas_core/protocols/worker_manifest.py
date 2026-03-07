"""Worker manifest protocol models (`workers/{agent_id}.yaml`)."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from .capability import CapabilityDef


class WorkerMetadata(BaseModel):
    id: str
    name: str
    version: str = "1.0"
    description: str | None = None
    source_repo: str | None = None
    source_revision: str | None = None
    tags: list[str] = Field(default_factory=list)


class WorkerRuntime(BaseModel):
    transport: Literal["process", "http", "oci", "mcp", "human"] = "process"
    adapter_config: dict[str, Any] = Field(default_factory=dict)
    timeout_seconds: int = Field(default=300, ge=1)
    stop_grace_seconds: int = Field(default=60, ge=1)


class WorkerSandbox(BaseModel):
    profile: Literal["standard", "restricted", "gvisor", "firecracker"] = "standard"
    filesystem: dict[str, Any] = Field(default_factory=dict)
    network_mode: Literal["egress-allowlist", "egress-deny-all", "unrestricted"] = "egress-allowlist"
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
    metadata: WorkerMetadata
    runtime: WorkerRuntime = Field(default_factory=WorkerRuntime)
    capabilities: list[CapabilityDef] = Field(default_factory=list)
    sandbox: WorkerSandbox = Field(default_factory=WorkerSandbox)
    checkpointing: WorkerCheckpointing = Field(default_factory=WorkerCheckpointing)
    observability: WorkerObservability = Field(default_factory=WorkerObservability)


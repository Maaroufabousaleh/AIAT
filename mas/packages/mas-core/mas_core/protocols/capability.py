"""Capability registry protocol models."""

from __future__ import annotations

from typing import Any, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, Field

from .enums import AgentRole


class CapabilityDef(BaseModel):
    """Canonical capability definition used by worker manifests and registry APIs."""

    id: UUID = Field(default_factory=uuid4)
    name: str
    version: str = "1.0"
    description: str | None = None
    input_schema: dict[str, Any] | None = None
    output_schema: dict[str, Any] | None = None
    risk_level: Literal["low", "medium", "high", "critical"] = "low"
    cost_model: dict[str, Any] | None = None
    required_tools: list[str] = Field(default_factory=list)
    required_role: AgentRole | None = None


class WorkerCapabilityRecord(BaseModel):
    """Registered worker entry returned by capability lookup APIs."""

    worker_id: str
    name: str
    role: AgentRole | None = None
    adapter_type: Literal["process", "http", "oci", "mcp", "human"] = "process"
    sandbox_profile: Literal["standard", "restricted", "gvisor", "firecracker"] = "standard"
    capabilities: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class CapabilitySearchRequest(BaseModel):
    name: str
    role: AgentRole | None = None
    min_sandbox_tier: int = Field(default=0, ge=0, le=3)


class CapabilitySearchResponse(BaseModel):
    query: CapabilitySearchRequest
    workers: list[WorkerCapabilityRecord] = Field(default_factory=list)
    count: int = 0


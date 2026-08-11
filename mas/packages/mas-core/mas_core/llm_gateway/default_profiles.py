"""Idempotent bootstrap definitions for AIAT's checked-in model profiles.

The runtime registry describes available model identities; this module seeds
only the governed profile declarations that are part of the AIAT product
itself. It never accepts arbitrary user input, never overwrites an
operator-owned profile or version, and reports a conflicting persisted row as
a blocked reconciliation result for operator repair.
"""

from __future__ import annotations

import hashlib
import inspect
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Iterable

PROFILE_BOOTSTRAP_SCHEMA = "aiat.model-profile-bootstrap.v1"


@dataclass(frozen=True, slots=True)
class ModelProfileSeedSpec:
    """One immutable profile/version declaration shipped by AIAT."""

    profile_id: str
    purpose: str
    provider_id: str
    exact_model_id: str
    version: str = "1"
    capabilities: tuple[str, ...] = ("tool_calling", "streaming")
    context_window: int = 0
    max_output_tokens: int = 0
    tool_calling: bool = True
    structured_output: bool = False
    vision: bool = False
    reasoning: bool = False
    streaming: bool = True
    embedding: bool = False
    cost_per_1k_input_usd: float = 0.0
    cost_per_1k_output_usd: float = 0.0
    privacy_class: str = "internal"
    regions: tuple[str, ...] = ("internal",)
    local: bool = False
    profile_status: str = "approved"
    version_status: str = "approved"
    evidence_refs: tuple[str, ...] = ()
    provider_settings: tuple[tuple[str, str], ...] = ()

    def profile_values(self) -> dict[str, Any]:
        return {
            "logical_profile_id": self.profile_id,
            "purpose": self.purpose,
            "approved_provider_ids": [self.provider_id],
            "required_capabilities": list(self.capabilities),
            "fallback_profile_ids": [],
            "status": self.profile_status,
            "owner": "aiat",
        }

    def version_values(self, *, profile_id: Any) -> dict[str, Any]:
        metadata = {
            "context_window": self.context_window,
            "max_output_tokens": self.max_output_tokens,
            "tool_calling": self.tool_calling,
            "structured_output": self.structured_output,
            "vision": self.vision,
            "reasoning": self.reasoning,
            "streaming": self.streaming,
            "embedding": self.embedding,
            "cost_per_1k_input_usd": self.cost_per_1k_input_usd,
            "cost_per_1k_output_usd": self.cost_per_1k_output_usd,
            "privacy_class": self.privacy_class,
            "regions": list(self.regions),
            "local": self.local,
            "bootstrap_schema": PROFILE_BOOTSTRAP_SCHEMA,
            "evidence_refs": list(self.evidence_refs),
        }
        return {
            "profile_id": profile_id,
            "version": self.version,
            "provider_id": self.provider_id,
            "exact_model_id": self.exact_model_id,
            "capabilities": list(self.capabilities),
            "provider_settings": dict(self.provider_settings),
            "status": self.version_status,
            "version_metadata": metadata,
        }


# This is the profile referenced by the checked-in coding and tester worker
# manifests.  The evidence file records an approved Phase 0B OpenCode run;
# the live route name is the current LiteLLM/OmniRoute alias.
DEFAULT_MODEL_PROFILE_SPECS: tuple[ModelProfileSeedSpec, ...] = (
    ModelProfileSeedSpec(
        profile_id="opencode-phase0b-coding",
        purpose="OpenCode coding and test execution through the AIAT gateway",
        provider_id="litellm",
        exact_model_id="omniroute-coding",
        capabilities=("tool_calling", "streaming"),
        context_window=131_072,
        max_output_tokens=16_384,
        tool_calling=True,
        streaming=True,
        privacy_class="internal",
        regions=("internal",),
        evidence_refs=(
            "docs/opencode/phase0b/1.17.13/live-certification-evidence.json",
        ),
        provider_settings=(
            ("backend", "litellm"),
            ("route_alias", "omniroute-coding"),
        ),
    ),
)


def _registry_profile_id(provider_id: str, model_id: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", f"{provider_id}-{model_id}".lower()).strip("-")
    suffix = hashlib.sha256(f"{provider_id}:{model_id}".encode()).hexdigest()[:8]
    return f"registry-{slug}-{suffix}"


def build_registry_model_profile_specs(registry: Any) -> tuple[ModelProfileSeedSpec, ...]:
    """Build deterministic approved declarations for registered model identities.

    Registry entries are already the checked-in provider/model catalogue. The
    generated profiles preserve that exact identity and capability metadata;
    they do not claim a live provider health or outage test. The explicit
    OpenCode profile remains the stable worker-facing name for its alias.
    """
    reserved_model_ids = {spec.exact_model_id for spec in DEFAULT_MODEL_PROFILE_SPECS}
    specs: list[ModelProfileSeedSpec] = list(DEFAULT_MODEL_PROFILE_SPECS)
    for model in sorted(registry.list_models(), key=lambda item: (item.provider, item.model_id)):
        if model.model_id in reserved_model_ids:
            continue
        capabilities: list[str] = []
        if model.supports_tools:
            capabilities.append("tool_calling")
        if model.supports_streaming:
            capabilities.append("streaming")
        if model.capabilities.supports_images:
            capabilities.append("vision")
        if model.capabilities.supports_reasoning:
            capabilities.append("reasoning")
        specs.append(
            ModelProfileSeedSpec(
                profile_id=_registry_profile_id(model.provider, model.model_id),
                purpose=f"Registered runtime model {model.model_id}",
                provider_id=model.provider,
                exact_model_id=model.model_id,
                capabilities=tuple(capabilities),
                context_window=int(model.max_context_tokens or 0),
                tool_calling=bool(model.supports_tools),
                vision=bool(model.capabilities.supports_images),
                reasoning=bool(model.capabilities.supports_reasoning),
                streaming=bool(model.supports_streaming),
                cost_per_1k_input_usd=float(model.cost_per_1m_input or 0.0) / 1000,
                cost_per_1k_output_usd=float(model.cost_per_1m_output or 0.0) / 1000,
                local=model.api_style.value == "cli",
                evidence_refs=(
                    "mas/packages/mas-core/mas_core/llm_gateway/providers",
                ),
                provider_settings=(
                    ("registry_model_id", model.model_id),
                    ("api_style", model.api_style.value),
                ),
            )
        )
    return tuple(specs)


def _required_methods(storage: Any) -> bool:
    return all(
        inspect.iscoroutinefunction(getattr(storage, name, None))
        for name in (
            "get_model_profile",
            "create_model_profile",
            "create_model_profile_version",
        )
    )


def _same_profile(existing: dict[str, Any], spec: ModelProfileSeedSpec) -> bool:
    return (
        str(existing.get("logical_profile_id")) == spec.profile_id
        and str(existing.get("purpose")) == spec.purpose
        and {str(item) for item in (existing.get("approved_provider_ids") or [])}
        == {spec.provider_id}
    )


def _same_version(existing: dict[str, Any], spec: ModelProfileSeedSpec) -> bool:
    return (
        str(existing.get("version")) == spec.version
        and str(existing.get("provider_id")) == spec.provider_id
        and str(existing.get("exact_model_id")) == spec.exact_model_id
    )


async def seed_model_profile_specs(
    storage: Any,
    specs: Iterable[ModelProfileSeedSpec] = DEFAULT_MODEL_PROFILE_SPECS,
) -> dict[str, Any]:
    """Idempotently persist checked-in profile declarations.

    Existing rows are never downgraded or mutated.  A missing profile or
    version is created; an existing matching row is retained; a mismatch is a
    blocked finding.  The result is safe to include in startup/seed evidence.
    """
    report: dict[str, Any] = {
        "schema_version": PROFILE_BOOTSTRAP_SCHEMA,
        "status": "pass",
        "created_profiles": 0,
        "created_versions": 0,
        "existing_profiles": 0,
        "existing_versions": 0,
        "conflicts": [],
        "profiles": [],
    }
    if not _required_methods(storage):
        report.update(
            status="blocked",
            reason="storage does not expose async model-profile persistence methods",
        )
        return report

    for spec in sorted(specs, key=lambda item: item.profile_id):
        existing = await storage.get_model_profile(spec.profile_id)
        if existing is None:
            profile = await storage.create_model_profile(**spec.profile_values())
            report["created_profiles"] += 1
            existing = {**profile, "versions": []}
        elif not _same_profile(existing, spec):
            report["status"] = "blocked"
            report["conflicts"].append(
                {
                    "profile_id": spec.profile_id,
                    "kind": "profile",
                    "reason": "persisted profile identity or provider differs from the checked-in declaration",
                }
            )
            continue
        else:
            report["existing_profiles"] += 1

        versions = list(existing.get("versions") or [])
        matching = [row for row in versions if str(row.get("version")) == spec.version]
        if matching:
            if not _same_version(matching[0], spec):
                report["status"] = "blocked"
                report["conflicts"].append(
                    {
                        "profile_id": spec.profile_id,
                        "version": spec.version,
                        "kind": "version",
                        "reason": "persisted profile version provider/model differs from the checked-in declaration",
                    }
                )
            else:
                report["existing_versions"] += 1
            report["profiles"].append(
                {"profile_id": spec.profile_id, "version": spec.version, "action": "existing"}
            )
            continue

        profile_id = existing.get("id")
        if profile_id is None:
            report["status"] = "blocked"
            report["conflicts"].append(
                {
                    "profile_id": spec.profile_id,
                    "version": spec.version,
                    "kind": "version",
                    "reason": "persisted profile has no database id",
                }
            )
            continue
        await storage.create_model_profile_version(
            **spec.version_values(profile_id=profile_id),
        )
        report["created_versions"] += 1
        report["profiles"].append(
            {"profile_id": spec.profile_id, "version": spec.version, "action": "created"}
        )

    return report


async def seed_default_model_profiles(storage: Any) -> dict[str, Any]:
    """Seed the checked-in default and registry-derived profile set."""
    # Import lazily so the profile definitions can be imported while the
    # provider registry is still constructing its global singleton.
    from .providers import MODEL_REGISTRY

    return await seed_model_profile_specs(storage, build_registry_model_profile_specs(MODEL_REGISTRY))

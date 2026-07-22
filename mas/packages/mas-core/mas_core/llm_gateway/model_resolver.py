"""Deterministic, constraint-based Model Profile resolver."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from .model_profiles import (
    ModelPolicyConstraints,
    ModelProfile,
    ModelProfileStatus,
    ModelResolutionError,
    ModelResolutionRequest,
    ModelResolutionSnapshot,
    RejectedModelCandidate,
)


@dataclass(frozen=True, slots=True)
class ResolutionResult:
    snapshot: ModelResolutionSnapshot
    fallback_profiles: tuple[str, ...]


class ModelProfileResolver:
    """Resolve only approved profiles satisfying all policy constraints."""

    def resolve(
        self,
        profiles: Iterable[ModelProfile],
        request: ModelResolutionRequest,
    ) -> ModelResolutionSnapshot:
        profile_map = {profile.profile_id: profile for profile in profiles}
        requested_lineage: set[str] | None = None
        if request.requested_profile_id is not None:
            requested = profile_map.get(request.requested_profile_id)
            if requested is None:
                raise ModelResolutionError(
                    "REQUESTED_PROFILE_NOT_FOUND",
                    f"Requested Model Profile {request.requested_profile_id!r} does not exist",
                )
            # A request may resolve only to the selected profile or its
            # explicitly declared fallback lineage.  Ranking an unrelated
            # approved profile is a policy bypass, not a fallback.
            requested_lineage = set()
            pending = [requested.profile_id]
            while pending:
                profile_id = pending.pop(0)
                if profile_id in requested_lineage:
                    continue
                requested_lineage.add(profile_id)
                profile = profile_map.get(profile_id)
                if profile is not None:
                    pending.extend(profile.fallback_profile_ids)
        constraints = ModelPolicyConstraints()
        preferred_profiles: list[str] = []
        preferred_models: list[str] = []
        for layer in request.layers:
            constraints = constraints.intersect(layer.constraints)
            preferred_profiles.extend(layer.preferred_profile_ids)
            preferred_models.extend(layer.preferred_exact_model_ids)
        required = (
            set(constraints.required_capabilities)
            | set(request.worker_required_capabilities)
            | set(request.steward_required_capabilities)
            | set(request.task_required_capabilities)
            | set(request.adapter_required_capabilities)
        )
        if constraints.require_streaming:
            required.add("streaming")
        if constraints.require_tool_calling:
            required.add("tool_calling")
        if constraints.require_structured_output:
            required.add("structured_output")
        if constraints.require_vision:
            required.add("vision")
        if constraints.require_reasoning:
            required.add("reasoning")

        candidates: list[tuple[tuple[int, int, str, str], ModelProfile, object]] = []
        rejected: list[RejectedModelCandidate] = []
        for profile in sorted(profile_map.values(), key=lambda item: item.profile_id):
            for version in sorted(profile.approved_versions(), key=lambda item: item.version, reverse=True):
                reasons: list[str] = []
                candidate_required = required | set(profile.required_capabilities)
                if requested_lineage is not None and profile.profile_id not in requested_lineage:
                    reasons.append("profile is outside the requested profile fallback lineage")
                if profile.status != ModelProfileStatus.APPROVED:
                    reasons.append("profile is not approved")
                if constraints.allowed_profile_ids is not None and profile.profile_id not in constraints.allowed_profile_ids:
                    reasons.append("profile is outside allowed profile intersection")
                if constraints.allowed_provider_ids is not None and version.provider_id not in constraints.allowed_provider_ids:
                    reasons.append("provider is outside allowed provider intersection")
                if version.provider_id in constraints.denied_provider_ids:
                    reasons.append("provider is denied")
                if constraints.allowed_exact_model_ids is not None and version.exact_model_id not in constraints.allowed_exact_model_ids:
                    reasons.append("model is outside allowed model intersection")
                if version.exact_model_id in constraints.denied_exact_model_ids:
                    reasons.append("model is denied")
                if constraints.local_only and not version.local:
                    reasons.append("local-only policy")
                if constraints.allowed_regions is not None and not (set(version.regions) & set(constraints.allowed_regions)):
                    reasons.append("region is not allowed")
                if constraints.privacy_class_at_most is not None:
                    order = list(type(version.privacy_class))
                    if order.index(version.privacy_class) > order.index(constraints.privacy_class_at_most):
                        reasons.append("privacy class exceeds policy")
                if version.context_window < max(constraints.minimum_context_window, request.prompt_tokens + request.expected_output_tokens):
                    reasons.append("context window is too small")
                if constraints.maximum_tokens is not None and request.expected_output_tokens > constraints.maximum_tokens:
                    reasons.append("expected output exceeds token policy")
                if version.max_output_tokens and request.expected_output_tokens > version.max_output_tokens:
                    reasons.append("expected output exceeds model limit")
                cost = version.estimate_cost(request.prompt_tokens, request.expected_output_tokens)
                maximum_cost = _minimum(request.budget_usd, constraints.maximum_cost_usd)
                if maximum_cost is not None and cost > maximum_cost:
                    reasons.append("estimated cost exceeds budget")
                if version.max_cost_usd is not None and cost > version.max_cost_usd:
                    reasons.append("estimated cost exceeds model profile limit")
                if version.max_tokens_per_request is not None and request.prompt_tokens + request.expected_output_tokens > version.max_tokens_per_request:
                    reasons.append("request exceeds model profile token limit")
                supports, missing = version.supports(candidate_required)
                if not supports:
                    reasons.extend(f"missing capability: {item}" for item in missing)
                if reasons:
                    rejected.append(RejectedModelCandidate(profile_id=profile.profile_id, version=version.version, exact_model_id=version.exact_model_id, reasons=tuple(sorted(set(reasons)))))
                    continue
                profile_rank = _rank(profile.profile_id, request.requested_profile_id, preferred_profiles, profile.fallback_profile_ids)
                model_rank = _rank(version.exact_model_id, None, preferred_models, ())
                candidates.append(((profile_rank, model_rank, profile.profile_id, version.version), profile, version))

        if not candidates:
            snapshot = ModelResolutionSnapshot(
                requested_profile_id=request.requested_profile_id,
                effective_constraints=constraints,
                required_capabilities=frozenset(sorted(required)),
                rejected_candidates=tuple(rejected),
                override_approval_id=request.override_approval_id,
                selection_reason="No approved model satisfies the policy intersection",
                policy_failure_code="NO_COMPLIANT_MODEL",
            )
            raise ModelResolutionError(
                "NO_COMPLIANT_MODEL",
                "No approved Model Profile satisfies the effective policy and capability requirements",
                rejected_candidates=rejected,
            )

        candidates.sort(key=lambda item: item[0])
        _, selected_profile, selected_version = candidates[0]
        resolved_required = required | set(selected_profile.required_capabilities)
        fallback_chain = tuple(
            f"{profile.profile_id}:{version.version}"
            for _, profile, version in candidates
        )
        return ModelResolutionSnapshot(
            requested_profile_id=request.requested_profile_id,
            resolved_profile_id=selected_profile.profile_id,
            resolved_profile_version=selected_version.version,
            provider_id=selected_version.provider_id,
            exact_model_id=selected_version.exact_model_id,
            api_version=selected_version.api_version,
            effective_constraints=constraints,
            effective_configuration=dict(selected_version.provider_settings),
            required_capabilities=frozenset(sorted(resolved_required)),
            capability_checks={capability: capability in selected_version.capabilities or getattr(selected_version, capability, False) for capability in sorted(resolved_required)},
            rejected_candidates=tuple(rejected),
            fallback_chain=fallback_chain,
            fallback_decisions=tuple({"candidate": item, "reason": "approved compliant fallback"} for item in fallback_chain[1:]),
            cost_estimate_usd=selected_version.estimate_cost(request.prompt_tokens, request.expected_output_tokens),
            override_approval_id=request.override_approval_id,
            selection_reason="Deterministic best compliant approved profile after policy intersection",
        )

    def dry_run(self, profiles: Iterable[ModelProfile], request: ModelResolutionRequest) -> dict[str, object]:
        try:
            snapshot = self.resolve(profiles, request)
            return {"authorized": True, "snapshot": snapshot.model_dump(mode="json")}
        except ModelResolutionError as exc:
            return {
                "authorized": False,
                "error": {"code": exc.code, "message": str(exc)},
                "rejected_candidates": [item.model_dump(mode="json") for item in exc.rejected_candidates],
            }


def _rank(value: str, requested: str | None, preferred: list[str], fallback: tuple[str, ...]) -> int:
    if requested and value == requested:
        return 0
    if value in preferred:
        return 1 + preferred.index(value)
    if value in fallback:
        return 100 + fallback.index(value)
    return 1000


def _minimum(left: float | None, right: float | None) -> float | None:
    if left is None:
        return right
    if right is None:
        return left
    return min(left, right)

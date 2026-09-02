"""Deterministic runtime/profile catalogue reconciliation tests."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from mas_core.llm_gateway import (
    MODEL_PROFILE_CATALOGUE_SCHEMA,
    ModelProfile,
    ModelProfileStatus,
    ModelProfileVersion,
    ModelResolutionRequest,
    build_model_profile_catalogue,
)
from mas_core.llm_gateway.model_resolver import ModelProfileResolver
from mas_core.llm_gateway.providers import ApiStyle, ModelEntry, ModelRegistry, ProviderConfig


def _registry() -> ModelRegistry:
    registry = ModelRegistry()
    registry.register_provider(
        ProviderConfig(provider_id="test", base_url="https://example.invalid")
    )
    registry.register(
        ModelEntry(
            model_id="test-model",
            provider="test",
            api_style=ApiStyle.CHAT_COMPLETIONS,
            endpoint="/v1/chat/completions",
            supports_tools=True,
        )
    )
    return registry


def _profile(*, model_id: str, provider_id: str = "test") -> ModelProfile:
    return ModelProfile(
        profile_id=f"profile-{model_id}",
        purpose="catalogue test",
        approved_provider_ids=frozenset({provider_id}),
        status=ModelProfileStatus.APPROVED,
        versions=(
            ModelProfileVersion(
                version="1.0.0",
                provider_id=provider_id,
                exact_model_id=model_id,
                tool_calling=True,
                capabilities=frozenset({"tool_calling"}),
                status=ModelProfileStatus.APPROVED,
            ),
        ),
    )


def test_catalogue_marks_registered_model_with_approved_profile():
    report = build_model_profile_catalogue([_profile(model_id="test-model")], _registry())
    assert report["schema_version"] == MODEL_PROFILE_CATALOGUE_SCHEMA
    assert report["registry_model_count"] == 1
    assert report["covered_profile_version_count"] == 1
    assert report["findings"] == []
    assert report["entries"][0]["profile_state"] == "approved_profile_present"


def test_catalogue_retains_unknown_profile_as_finding():
    report = build_model_profile_catalogue([_profile(model_id="missing-model")], _registry())
    assert report["findings"][0]["code"] == "PROFILE_MODEL_NOT_REGISTERED"
    stale = next(entry for entry in report["entries"] if entry["model_id"] == "missing-model")
    assert stale["profile_state"] == "profile_not_registered"


def test_catalogue_retains_provider_mismatch():
    report = build_model_profile_catalogue([_profile(model_id="test-model", provider_id="other")], _registry())
    assert report["findings"][0]["code"] == "PROFILE_PROVIDER_MISMATCH"


def test_catalogue_matches_explicit_gateway_profile_identity_alias() -> None:
    registry = ModelRegistry()
    registry.register_provider(
        ProviderConfig(provider_id="litellm", base_url="https://example.invalid")
    )
    registry.register(
        ModelEntry(
            model_id="omniroute-coding",
            provider="litellm",
            api_style=ApiStyle.CHAT_COMPLETIONS,
            endpoint="/v1/chat/completions",
            extra={
                "profile_identity_aliases": [
                    {"provider": "aiat", "model_id": "aiat/omniroute-coding"}
                ]
            },
        )
    )

    report = build_model_profile_catalogue(
        [_profile(model_id="aiat/omniroute-coding", provider_id="aiat")],
        registry,
    )

    assert report["findings"] == []
    assert report["covered_profile_version_count"] == 1
    assert report["entries"][0]["profile_state"] == "approved_profile_present"


def test_unknown_context_window_does_not_reject_a_governed_profile():
    profile = _profile(model_id="test-model")
    version = profile.versions[0].model_copy(update={"context_window": 0})
    profile = profile.model_copy(update={"versions": (version,)})
    request = ModelResolutionRequest(task_type="catalogue", prompt_tokens=2_000)

    resolved = ModelProfileResolver().resolve([profile], request)

    assert resolved.exact_model_id == "test-model"


def test_catalogue_live_runner_fails_closed_without_orchestrator_configuration():
    repo_root = Path(__file__).resolve().parents[3]
    env = {
        key: value
        for key, value in os.environ.items()
        if key not in {"AIAT_ORCHESTRATOR_URL", "ORCHESTRATOR_API_URL"}
    }
    result = subprocess.run(
        [
            sys.executable,
            "scripts/check_model_profile_catalogue.py",
            "--live",
            "--json",
            "--require-approved",
        ],
        cwd=repo_root,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 2
    payload = json.loads(result.stdout)
    assert payload["mode"] == "live"
    assert payload["status"] == "blocked"

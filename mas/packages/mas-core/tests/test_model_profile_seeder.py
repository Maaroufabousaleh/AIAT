"""Tests for the idempotent checked-in model-profile bootstrap."""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest

from mas_core.llm_gateway import (
    DEFAULT_MODEL_PROFILE_SPECS,
    MODEL_REGISTRY,
    build_registry_model_profile_specs,
    seed_default_model_profiles,
    seed_model_profile_specs,
)


class _ProfileStorage:
    def __init__(self) -> None:
        self.profiles: dict[str, dict] = {}

    async def get_model_profile(self, profile_id: str) -> dict | None:
        return self.profiles.get(profile_id)

    async def create_model_profile(self, **values: object) -> dict:
        row = {"id": uuid4(), **values, "versions": []}
        self.profiles[str(values["logical_profile_id"])] = row
        return row

    async def create_model_profile_version(self, **values: object) -> dict:
        profile_id = UUID(str(values["profile_id"]))
        row = dict(values, id=uuid4())
        for profile in self.profiles.values():
            if profile["id"] == profile_id:
                profile["versions"].append(row)
                return row
        raise AssertionError("profile was not found")


@pytest.mark.asyncio
async def test_default_profile_bootstrap_is_idempotent_and_evidence_referenced() -> None:
    storage = _ProfileStorage()

    first = await seed_default_model_profiles(storage)
    second = await seed_default_model_profiles(storage)

    assert first["schema_version"] == "aiat.model-profile-bootstrap.v1"
    assert first["status"] == "pass"
    expected_count = len(build_registry_model_profile_specs(MODEL_REGISTRY))
    assert expected_count == 93
    assert first["created_profiles"] == expected_count
    assert first["created_versions"] == expected_count
    assert second["status"] == "pass"
    assert second["created_profiles"] == 0
    assert second["created_versions"] == 0
    assert second["existing_profiles"] == expected_count
    assert second["existing_versions"] == expected_count

    profile = storage.profiles["opencode-phase0b-coding"]
    assert profile["status"] == "approved"
    version = profile["versions"][0]
    assert version["provider_id"] == "litellm"
    assert version["exact_model_id"] == "omniroute-coding"
    assert version["status"] == "approved"
    assert version["version_metadata"]["evidence_refs"]


@pytest.mark.asyncio
async def test_profile_bootstrap_blocks_on_conflicting_operator_row() -> None:
    storage = _ProfileStorage()
    await storage.create_model_profile(
        logical_profile_id="opencode-phase0b-coding",
        purpose="operator-owned alternate",
        approved_provider_ids=["other"],
        required_capabilities=[],
        fallback_profile_ids=[],
        status="draft",
        owner="operator",
    )

    report = await seed_default_model_profiles(storage)

    assert report["status"] == "blocked"
    assert report["created_profiles"] == len(MODEL_REGISTRY) - 1
    assert report["conflicts"][0]["kind"] == "profile"
    assert storage.profiles["opencode-phase0b-coding"]["versions"] == []


@pytest.mark.asyncio
async def test_profile_bootstrap_requires_async_storage_contract() -> None:
    report = await seed_default_model_profiles(object())

    assert report["status"] == "blocked"
    assert "storage" in report["reason"]


def test_internal_coding_alias_is_registered_for_catalogue_reconciliation() -> None:
    entry = MODEL_REGISTRY.get(DEFAULT_MODEL_PROFILE_SPECS[0].exact_model_id)

    assert entry is not None
    assert entry.provider == "litellm"
    assert entry.extra["route_alias"] == "omniroute-coding"


def test_registry_profile_specs_cover_each_registered_model_once() -> None:
    specs = build_registry_model_profile_specs(MODEL_REGISTRY)

    assert len(specs) == len(MODEL_REGISTRY)
    assert len({spec.profile_id for spec in specs}) == len(specs)
    assert {spec.exact_model_id for spec in specs} == set(MODEL_REGISTRY.model_ids())
    assert all(spec.profile_status == "approved" for spec in specs)


@pytest.mark.asyncio
async def test_generic_profile_spec_set_is_supported() -> None:
    storage = _ProfileStorage()
    spec = DEFAULT_MODEL_PROFILE_SPECS[0]

    report = await seed_model_profile_specs(storage, (spec,))

    assert report["profiles"] == [
        {"profile_id": spec.profile_id, "version": spec.version, "action": "created"}
    ]

"""Object-store orphan, expiry, garbage-collection, and hold boundaries."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from mas_core.memory import (
    InMemoryObjectStore,
    LegalHoldSnapshot,
    LifecycleCanonicalObject,
    LifecycleInventoryObject,
    ObjectLifecycleError,
    execute_object_lifecycle,
    plan_object_lifecycle,
)

EVALUATED_AT = datetime(2026, 8, 19, 4, 0, tzinfo=UTC)


async def _upload(store: InMemoryObjectStore, project_id: str, key: str, payload: bytes):
    return await store.upload(project_id, key, payload, content_type="application/octet-stream")


def _canonical(ref, *, retention_until=None, legal_hold=False) -> LifecycleCanonicalObject:
    return LifecycleCanonicalObject(
        key=ref.key,
        sha256=ref.sha256,
        size_bytes=ref.size_bytes,
        retention_until=retention_until,
        legal_hold=legal_hold,
    )


@pytest.mark.asyncio
async def test_lifecycle_preview_and_confirmed_cleanup_are_conservative() -> None:
    store = InMemoryObjectStore(bucket="objects")
    project_id = "lifecycle-project"
    keep = await _upload(store, project_id, "keep.bin", b"keep")
    expired = await _upload(store, project_id, "expired.bin", b"expired")
    held = await _upload(store, project_id, "held.bin", b"held")
    orphan = await _upload(store, project_id, "orphan.bin", b"orphan")
    orphan_held = await _upload(store, project_id, "orphan-held.bin", b"orphan-held")
    drift = await _upload(store, project_id, "drift.bin", b"drift")
    holds = LegalHoldSnapshot.create(
        source_ref="hold-registry://lifecycle-fixture",
        hold_keys=(held.key, orphan_held.key),
    )
    canonical = [
        _canonical(keep, retention_until=EVALUATED_AT + timedelta(days=1)),
        _canonical(expired, retention_until=EVALUATED_AT - timedelta(seconds=1)),
        _canonical(held, retention_until=EVALUATED_AT - timedelta(seconds=1)),
        LifecycleCanonicalObject(
            key=drift.key,
            sha256=drift.sha256,
            size_bytes=drift.size_bytes + 1,
            retention_until=EVALUATED_AT - timedelta(seconds=1),
        ),
    ]

    plan = plan_object_lifecycle(
        project_id=project_id,
        bucket="objects",
        inventory=tuple(
            LifecycleInventoryObject(key=row["key"], size_bytes=row["size"])
            for row in await store.list_objects(project_id, bucket="objects")
        ),
        canonical=canonical,
        evaluated_at=EVALUATED_AT,
        legal_hold_snapshot=holds,
    )

    assert plan.orphan_keys == (orphan_held.key, orphan.key)
    assert plan.expired_keys == (drift.key, expired.key, held.key)
    assert plan.held_keys == (held.key, orphan_held.key)
    assert plan.size_mismatch_keys == (drift.key,)
    assert plan.delete_keys == (expired.key, orphan.key)
    assert plan.retain_keys == (drift.key, held.key, keep.key, orphan_held.key)
    plan.verify()

    with pytest.raises(ObjectLifecycleError, match="explicit confirmation"):
        await execute_object_lifecycle(store, plan, legal_hold_snapshot=holds)
    assert await store.exists(project_id, "expired.bin", bucket="objects") is True

    execution = await execute_object_lifecycle(
        store,
        plan,
        legal_hold_snapshot=holds,
        confirm=True,
    )
    assert execution.status == "pass"
    assert execution.cleanup_verified is True
    assert execution.mutation_performed is True
    assert execution.deleted_keys == (expired.key, orphan.key)
    assert await store.exists(project_id, "expired.bin", bucket="objects") is False
    assert await store.exists(project_id, "orphan.bin", bucket="objects") is False
    assert await store.exists(project_id, "held.bin", bucket="objects") is True
    assert await store.exists(project_id, "orphan-held.bin", bucket="objects") is True
    assert await store.exists(project_id, "drift.bin", bucket="objects") is True


@pytest.mark.asyncio
async def test_lifecycle_refuses_inventory_drift_before_mutation() -> None:
    store = InMemoryObjectStore(bucket="objects")
    project_id = "lifecycle-drift"
    expired = await _upload(store, project_id, "expired.bin", b"expired")
    holds = LegalHoldSnapshot.create(source_ref="hold-registry://drift", hold_keys=())
    plan = plan_object_lifecycle(
        project_id=project_id,
        bucket="objects",
        inventory=(LifecycleInventoryObject(key=expired.key, size_bytes=expired.size_bytes),),
        canonical=(
            _canonical(expired, retention_until=EVALUATED_AT - timedelta(seconds=1)),
        ),
        evaluated_at=EVALUATED_AT,
        legal_hold_snapshot=holds,
    )
    await _upload(store, project_id, "new-after-preview.bin", b"new")

    with pytest.raises(ObjectLifecycleError, match="inventory changed"):
        await execute_object_lifecycle(store, plan, legal_hold_snapshot=holds, confirm=True)
    assert await store.exists(project_id, "expired.bin", bucket="objects") is True


def test_lifecycle_rejects_duplicate_or_out_of_scope_rows() -> None:
    holds = LegalHoldSnapshot.create(source_ref="hold-registry://invalid", hold_keys=())
    inventory_type = LifecycleInventoryObject(key="lifecycle-invalid/a", size_bytes=1)
    canonical = LifecycleCanonicalObject(
        key="lifecycle-invalid/a",
        sha256="0" * 64,
        size_bytes=1,
    )
    with pytest.raises(ObjectLifecycleError, match="duplicate"):
        plan_object_lifecycle(
            project_id="lifecycle-invalid",
            bucket="objects",
            inventory=(inventory_type, inventory_type),
            canonical=(canonical,),
            evaluated_at=EVALUATED_AT,
            legal_hold_snapshot=holds,
        )
    with pytest.raises(ObjectLifecycleError, match="outside"):
        plan_object_lifecycle(
            project_id="lifecycle-invalid",
            bucket="objects",
            inventory=(LifecycleInventoryObject(key="other/a", size_bytes=1),),
            canonical=(),
            evaluated_at=EVALUATED_AT,
            legal_hold_snapshot=holds,
        )

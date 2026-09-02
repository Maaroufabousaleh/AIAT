"""Governed object-store migration workflow tests."""

from __future__ import annotations

import pytest

from mas_core.memory import (
    OBJECT_STORE_MIGRATION_SCHEMA,
    InMemoryObjectStore,
    ObjectStoreMigrationError,
    ObjectStoreMigrationWorkflow,
)


@pytest.mark.asyncio
async def test_migration_inventory_copy_dual_write_cutover_and_rollback() -> None:
    source = InMemoryObjectStore(bucket="source")
    target = InMemoryObjectStore(bucket="target")
    project_id = "migration-project"
    refs = [
        await source.upload(project_id, "artifacts/one.txt", b"one"),
        await source.upload(project_id, "artifacts/two.txt", b"two"),
    ]
    workflow = ObjectStoreMigrationWorkflow.create(
        migration_id="migration-fixture",
        project_id=project_id,
        source_adapter_type="in-memory-fixture",
        target_adapter_type="in-memory-fixture",
        source_bucket="source",
        target_bucket="target",
        dual_write_required=True,
    )

    manifest = await workflow.inventory(source, refs, actor="inventory", actor_kind="system")
    assert manifest.project_id == project_id
    await workflow.copy(source, target, actor="copy", actor_kind="system")
    dual_write = await workflow.dual_write(
        source,
        target,
        key="artifacts/new.txt",
        payload=b"new",
        actor="writer",
        actor_kind="system",
    )
    assert dual_write.status == "PASS"
    assert workflow.status == "DUAL_WRITE_READY"

    workflow.cutover(actor="operator", actor_kind="human", confirm=True)
    assert workflow.status == "CUTOVER"
    assert workflow.active_bucket == "target"
    workflow.rollback(
        actor="operator",
        actor_kind="human",
        confirm=True,
        reason="restore source while target retention is reviewed",
    )
    assert workflow.status == "ROLLED_BACK"
    assert workflow.active_bucket == "source"
    assert len(workflow.history) == 5
    evidence = workflow.as_dict()
    assert evidence["schema_version"] == OBJECT_STORE_MIGRATION_SCHEMA
    assert evidence["manifest"]["manifest_sha256"]
    assert evidence["restore_verification"]["status"] == "pass"
    assert evidence["dual_writes"][0]["status"] == "PASS"


@pytest.mark.asyncio
async def test_cutover_requires_dual_write_evidence_when_enabled() -> None:
    source = InMemoryObjectStore(bucket="source")
    target = InMemoryObjectStore(bucket="target")
    project_id = "migration-project"
    ref = await source.upload(project_id, "artifact.bin", b"payload")
    workflow = ObjectStoreMigrationWorkflow.create(
        migration_id="migration-no-dual-write",
        project_id=project_id,
        source_adapter_type="in-memory-fixture",
        target_adapter_type="in-memory-fixture",
        source_bucket="source",
        target_bucket="target",
        dual_write_required=True,
    )
    await workflow.inventory(source, [ref], actor="inventory")
    await workflow.copy(source, target, actor="copy")

    with pytest.raises(ObjectStoreMigrationError, match="DUAL_WRITE_READY"):
        workflow.cutover(actor="operator", actor_kind="human", confirm=True)


@pytest.mark.asyncio
async def test_cutover_and_rollback_require_human_confirmation() -> None:
    source = InMemoryObjectStore(bucket="source")
    target = InMemoryObjectStore(bucket="target")
    project_id = "migration-project"
    ref = await source.upload(project_id, "artifact.bin", b"payload")
    workflow = ObjectStoreMigrationWorkflow.create(
        migration_id="migration-authority",
        project_id=project_id,
        source_adapter_type="in-memory-fixture",
        target_adapter_type="in-memory-fixture",
        source_bucket="source",
        target_bucket="target",
    )
    await workflow.inventory(source, [ref], actor="inventory")
    await workflow.copy(source, target, actor="copy")

    with pytest.raises(ObjectStoreMigrationError, match="human operator"):
        workflow.cutover(actor="worker", actor_kind="system", confirm=True)
    with pytest.raises(ObjectStoreMigrationError, match="explicit confirmation"):
        workflow.cutover(actor="operator", actor_kind="human", confirm=False)

    workflow.cutover(actor="operator", actor_kind="human", confirm=True)
    with pytest.raises(ObjectStoreMigrationError, match="human operator"):
        workflow.rollback(
            actor="worker",
            actor_kind="system",
            confirm=True,
            reason="automated rollback",
        )


def test_migration_plan_rejects_same_store() -> None:
    with pytest.raises(ObjectStoreMigrationError, match="must differ"):
        ObjectStoreMigrationWorkflow.create(
            migration_id="same-store",
            project_id="project",
            source_adapter_type="s3-compatible",
            target_adapter_type="s3-compatible",
            source_bucket="same",
            target_bucket="same",
        )

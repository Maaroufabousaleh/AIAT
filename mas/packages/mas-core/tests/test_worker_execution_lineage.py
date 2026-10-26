from __future__ import annotations

from mas_core.worker_registry.execution_lineage import (
    SHELL_EXECUTION_LINEAGE_KEY,
    WorkerExecutionLineageError,
    build_worker_execution_config_snapshot,
    resolve_worker_execution_config_snapshot,
    worker_execution_config_snapshot_sha256,
    worker_shell_content_hash,
    worker_shell_execution_lineage_blocker,
)


def test_execution_config_snapshot_stores_references_not_resolved_secret_values() -> None:
    snapshot = build_worker_execution_config_snapshot(
        "process",
        {
            "command": ["worker", "--mode", "bounded"],
            "environment_refs": {"WORKER_TOKEN": "AIAT_WORKER_TOKEN"},
            "tool_grants": ["file_write"],
        },
        sandbox_profile="gvisor",
    )
    secret_value = "synthetic-runtime-secret"
    config, resolved_values, refs = resolve_worker_execution_config_snapshot(
        snapshot,
        environment={"AIAT_WORKER_TOKEN": secret_value},
    )

    assert config["environment"] == {"WORKER_TOKEN": secret_value}
    assert resolved_values == {"environment:WORKER_TOKEN": secret_value}
    assert refs == {"environment:WORKER_TOKEN": "AIAT_WORKER_TOKEN"}
    assert secret_value not in str(snapshot)
    assert "tool_grants" not in snapshot["config"]
    assert snapshot["sandbox_profile"] == "gvisor"


def test_execution_config_snapshot_resolves_header_reference_without_persisting_value() -> None:
    snapshot = build_worker_execution_config_snapshot(
        "http",
        {
            "base_url": "https://runtime.internal",
            "headers": {"X-AIAT-Client": "worker"},
            "header_secret_refs": {"X-AIAT-Token": "AIAT_RUNTIME_TOKEN"},
        },
    )
    secret_value = "synthetic-header-secret"
    config, resolved_values, refs = resolve_worker_execution_config_snapshot(
        snapshot,
        environment={"AIAT_RUNTIME_TOKEN": secret_value},
    )

    assert config["headers"] == {
        "X-AIAT-Client": "worker",
        "X-AIAT-Token": secret_value,
    }
    assert resolved_values == {"header:X-AIAT-Token": secret_value}
    assert refs == {"header:X-AIAT-Token": "AIAT_RUNTIME_TOKEN"}
    assert secret_value not in str(snapshot)


def test_execution_config_snapshot_rejects_inline_secret_material() -> None:
    credential_url = "https://user:" + "synthetic" + "@runtime.internal"
    for transport, config in (
        ("process", {"command": ["worker"], "environment": {"TOKEN": "raw-secret"}}),
        ("http", {"base_url": "https://runtime.internal", "headers": {"Authorization": "Bearer raw-secret"}}),
        ("http", {"base_url": credential_url}),
        ("process", {"command": ["worker", f"--endpoint={credential_url}"]}),
        ("process", {"command": ["worker", "--endpoint=https://runtime.internal?api_key=raw-secret"]}),
        ("process", {"command": ["worker", "--api-key", "raw-secret"]}),
        ("process", {"command": "worker --token raw-secret"}),
    ):
        try:
            build_worker_execution_config_snapshot(transport, config)
        except WorkerExecutionLineageError as exc:
            assert exc.reason_code in {
                "worker_runtime_config_inline_environment_rejected",
                "worker_runtime_config_inline_secret_rejected",
            }
        else:  # pragma: no cover - assertion gives a focused schema failure
            raise AssertionError("inline secret configuration was accepted")


def test_execution_config_snapshot_rejects_secret_values_in_secret_references() -> None:
    secret_value = "sk-" + "a" * 24
    try:
        build_worker_execution_config_snapshot("oci", {"secret_refs": [secret_value]})
    except WorkerExecutionLineageError as exc:
        assert exc.reason_code == "worker_runtime_config_inline_secret_rejected"
    else:  # pragma: no cover - assertion gives a focused schema failure
        raise AssertionError("inline secret in secret_refs was accepted")

    try:
        build_worker_execution_config_snapshot("oci", {"secret_refs": ["reference with spaces"]})
    except WorkerExecutionLineageError as exc:
        assert exc.reason_code == "worker_runtime_config_reference_invalid"
    else:  # pragma: no cover - assertion gives a focused schema failure
        raise AssertionError("non-opaque secret reference was accepted")


def test_worker_shell_lineage_binds_snapshot_hash_worker_and_adapter() -> None:
    snapshot = build_worker_execution_config_snapshot(
        "oci",
        {"image": "worker@sha256:" + "a" * 64},
        sandbox_profile="gvisor",
    )
    shell = {
        "id": "shell-1",
        "worker_id": "worker-1",
        "version": "candidate-1",
        "identity_json": {"worker_id": "worker-1"},
        "capabilities_json": {},
        "permissions_json": {},
        "model_mode": "none",
        "provenance_json": {
            "candidate_id": "candidate-1",
            "runtime_adapter_id": "adapter-1",
            "runtime_adapter": {
                "id": "adapter-1",
                "version": "adapter-v1",
                "adapter_type": "oci",
                "transport_type": "oci",
                "runtime_api_version": None,
                "implementation_ref": "worker@sha256:fixture",
                "content_hash": "adapter-content-hash",
            },
            SHELL_EXECUTION_LINEAGE_KEY: {
                "sha256": worker_execution_config_snapshot_sha256(snapshot),
                "snapshot": snapshot,
            },
        },
    }
    shell["content_hash"] = worker_shell_content_hash(shell)
    adapter = {
        "id": "adapter-1",
        "worker_id": "worker-1",
        "version": "adapter-v1",
        "adapter_type": "oci",
        "transport_type": "oci",
        "runtime_api_version": None,
        "implementation_ref": "worker@sha256:fixture",
        "content_hash": "adapter-content-hash",
    }

    assert worker_shell_execution_lineage_blocker(
        shell=shell,
        adapter=adapter,
        worker_id="worker-1",
        adapter_id="adapter-1",
    ) is None
    assert worker_shell_execution_lineage_blocker(
        shell=shell,
        adapter={**adapter, "content_hash": "different-adapter-content-hash"},
        worker_id="worker-1",
        adapter_id="adapter-1",
    ) == "worker_runtime_lineage_identity_mismatch"

    assert worker_shell_execution_lineage_blocker(
        shell=shell,
        adapter=adapter,
        worker_id="worker-1",
        adapter_id="adapter-other",
    ) == "worker_runtime_lineage_identity_mismatch"

    tampered_shell = {**shell, "provenance_json": dict(shell["provenance_json"])}
    tampered_provenance = dict(tampered_shell["provenance_json"])
    tampered_binding = dict(tampered_provenance[SHELL_EXECUTION_LINEAGE_KEY])
    tampered_snapshot = dict(tampered_binding["snapshot"])
    tampered_config = dict(tampered_snapshot["config"])
    tampered_config["image"] = "worker@sha256:" + "b" * 64
    tampered_snapshot["config"] = tampered_config
    tampered_binding["snapshot"] = tampered_snapshot
    tampered_provenance[SHELL_EXECUTION_LINEAGE_KEY] = tampered_binding
    tampered_shell["provenance_json"] = tampered_provenance
    assert worker_shell_execution_lineage_blocker(
        shell=tampered_shell,
        adapter=adapter,
        worker_id="worker-1",
        adapter_id="adapter-1",
    ) == "worker_runtime_lineage_integrity_invalid"


def test_worker_shell_without_historical_execution_snapshot_fails_closed() -> None:
    assert worker_shell_execution_lineage_blocker(
        shell={"worker_id": "worker-1", "provenance_json": {}},
        adapter={"worker_id": "worker-1", "transport_type": "oci"},
        worker_id="worker-1",
        adapter_id="adapter-1",
    ) == "worker_runtime_lineage_unavailable"

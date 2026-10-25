"""Tests for the canonical sandbox policy and host-runtime selection."""

from __future__ import annotations

import pytest

from mas_core.protocols.worker_manifest import WorkerManifest
from mas_core.sandbox_policy import (
    HARDENED_SANDBOX_CLASSES,
    WorkerIsolationPolicyError,
    canonical_sandbox_class,
    is_hardened_sandbox,
    required_runtime_for_sandbox,
    runtime_implements_sandbox,
    worker_requires_hardened_isolation,
)
from mas_core.worker_registry.host_registry import _normalize_sandbox_runtime
from mas_core.worker_registry.placement import (
    HostCapacity,
    WorkerHostSnapshot,
    WorkerPlacementRequest,
    evaluate_host,
)
from mas_core.worker_registry.runtime_adapters import OCIAdapter, adapter_for_transport


def test_legacy_profiles_resolve_to_canonical_policy_classes() -> None:
    assert canonical_sandbox_class("standard") == "trusted"
    assert canonical_sandbox_class("restricted") == "trusted"
    assert canonical_sandbox_class("gvisor") == "sandboxed"
    assert canonical_sandbox_class("firecracker") == "vm_isolated"
    assert {"sandboxed", "vm_isolated"} == HARDENED_SANDBOX_CLASSES
    assert is_hardened_sandbox("gvisor") is True
    assert is_hardened_sandbox("restricted") is False
    assert required_runtime_for_sandbox("gvisor") == "runsc"
    assert required_runtime_for_sandbox("vm_isolated") == "kata"


def test_worker_isolation_floor_treats_external_workers_as_untrusted() -> None:
    assert worker_requires_hardened_isolation(
        {
            "source_repo": "https://example.invalid/worker",
            "sandbox_profile": "trusted",
            "adapter_config": {},
        }
    ) is True
    assert worker_requires_hardened_isolation(
        {
            "runtime_tier": "external",
            "sandbox_profile": "trusted",
            "adapter_config": {"runtime_tier": "internal"},
        }
    ) is True
    assert worker_requires_hardened_isolation(
        {
            "runtime_tier": "builtin",
            "source_repo": "local",
            "sandbox_profile": "trusted",
            "adapter_config": {
                "runtime_tier": "builtin",
                "legacy_manifest": {"runtime_tier": "external"},
            },
        }
    ) is True
    assert worker_requires_hardened_isolation(
        {"source_repo": "LOCAL", "sandbox_profile": "trusted", "adapter_config": {}}
    ) is True
    assert worker_requires_hardened_isolation(
        {
            "sandbox_profile": "gvisor",
            "adapter_config": {"execution_policy": {"trust": "trusted"}},
        }
    ) is True


def test_worker_isolation_floor_preserves_trusted_internal_and_fails_closed() -> None:
    assert worker_requires_hardened_isolation(
        {"source_repo": "local", "sandbox_profile": "trusted", "adapter_config": {}}
    ) is False
    assert worker_requires_hardened_isolation(
        {
            "source_repo": "",
            "sandbox_profile": "trusted",
            "adapter_config": {
                "execution_policy": {"trust": "trusted", "minimum_isolation": "trusted"}
            },
        }
    ) is False

    with pytest.raises(WorkerIsolationPolicyError) as invalid_profile:
        worker_requires_hardened_isolation(
            {"sandbox_profile": "unknown", "adapter_config": {}}
        )
    assert invalid_profile.value.reason_code == "worker_sandbox_profile_invalid"

    with pytest.raises(WorkerIsolationPolicyError) as invalid_execution:
        worker_requires_hardened_isolation(
            {"sandbox_profile": "trusted", "adapter_config": {"execution_policy": []}}
        )
    assert invalid_execution.value.reason_code == "worker_execution_policy_invalid"

    with pytest.raises(WorkerIsolationPolicyError) as invalid_adapter_config:
        worker_requires_hardened_isolation(
            {"sandbox_profile": "trusted", "adapter_config": []}  # type: ignore[dict-item]
        )
    assert invalid_adapter_config.value.reason_code == "worker_execution_policy_invalid"

    with pytest.raises(WorkerIsolationPolicyError) as invalid_hardened_execution:
        worker_requires_hardened_isolation(
            {
                "sandbox_profile": "gvisor",
                "adapter_config": {"execution_policy": []},
            }
        )
    assert invalid_hardened_execution.value.reason_code == "worker_execution_policy_invalid"


def test_worker_manifest_accepts_canonical_class_without_rewriting_legacy_yaml() -> None:
    canonical = WorkerManifest.model_validate(
        {
            "metadata": {"id": "kata-worker", "name": "Kata worker"},
            "sandbox": {"profile": "vm_isolated"},
        }
    )
    legacy = WorkerManifest.model_validate(
        {
            "metadata": {"id": "gvisor-worker", "name": "gVisor worker"},
            "sandbox": {"profile": "gvisor"},
        }
    )

    assert canonical.sandbox.profile == "vm_isolated"
    assert canonical.sandbox.sandbox_class == "vm_isolated"
    assert legacy.sandbox.profile == "gvisor"
    assert legacy.sandbox.sandbox_class == "sandboxed"


def test_vm_isolated_oci_selection_requires_kata_and_never_uses_runc() -> None:
    image = "example/worker@sha256:" + "a" * 64
    adapter = adapter_for_transport(
        "oci",
        worker_id="kata-worker",
        config={"image": image, "sandbox_profile": "vm_isolated"},
    )

    assert isinstance(adapter, OCIAdapter)
    assert adapter.sandbox_class == "vm_isolated"
    assert adapter.sandbox_runtime == "kata"
    assert adapter.command[adapter.command.index("--runtime") + 1] == "kata"
    assert "runc" not in adapter.command
    assert "runsc" not in adapter.command

    with pytest.raises(ValueError, match="kata"):
        OCIAdapter(
            image,
            worker_id="kata-worker",
            sandbox_profile="vm_isolated",
            sandbox_runtime="runsc",
        )


def test_vm_isolated_oci_selection_accepts_discovered_kata_runtime_name() -> None:
    image = "example/worker@sha256:" + "b" * 64
    adapter = OCIAdapter(
        image,
        worker_id="kata-qemu-worker",
        sandbox_profile="vm_isolated",
        sandbox_runtime="kata-qemu",
    )

    assert adapter.sandbox_runtime == "kata-qemu"
    assert adapter.command[adapter.command.index("--runtime") + 1] == "kata-qemu"


def test_trusted_oci_uses_runc_only_with_an_explicit_trusted_policy() -> None:
    image = "example/worker@sha256:" + "d" * 64

    with pytest.raises(ValueError, match="immutable trusted execution policy"):
        adapter_for_transport(
            "oci",
            worker_id="unapproved-trusted-worker",
            config={"image": image, "sandbox_profile": "trusted"},
        )
    with pytest.raises(ValueError, match="immutable trusted execution policy"):
        adapter_for_transport(
            "oci",
            worker_id="untrusted-runc-worker",
            config={
                "image": image,
                "sandbox_profile": "trusted",
                "execution_policy": {
                    "trust": "untrusted",
                    "minimum_isolation": "sandboxed",
                },
            },
        )

    adapter = adapter_for_transport(
        "oci",
        worker_id="reviewed-trusted-worker",
        config={
            "image": image,
            "sandbox_profile": "trusted",
            "execution_policy": {
                "trust": "trusted",
                "minimum_isolation": "trusted",
            },
            "bidirectional_tool_protocol": True,
        },
    )

    assert isinstance(adapter, OCIAdapter)
    assert adapter.sandbox_class == "trusted"
    assert adapter.sandbox_runtime == "runc"
    assert adapter.bidirectional_tool_protocol is True
    assert adapter.command[adapter.command.index("--runtime") + 1] == "runc"
    assert adapter.command[adapter.command.index("--network") + 1] == "none"
    assert "--cap-drop" in adapter.command and "ALL" in adapter.command
    assert "--security-opt" in adapter.command and "no-new-privileges" in adapter.command
    assert "--read-only" in adapter.command

    with pytest.raises(ValueError, match="trusted OCI execution requires"):
        OCIAdapter(
            image,
            worker_id="unapproved-trusted-worker",
            sandbox_profile="trusted",
        )


def test_oci_runtime_cannot_use_runc_for_hardened_profiles_or_claim_trusted_for_them() -> None:
    image = "example/worker@sha256:" + "e" * 64
    with pytest.raises(ValueError, match="requires the runsc runtime"):
        OCIAdapter(
            image,
            worker_id="sandboxed-runc-worker",
            sandbox_profile="sandboxed",
            sandbox_runtime="runc",
        )
    with pytest.raises(ValueError, match="trusted_worker may only be set"):
        OCIAdapter(
            image,
            worker_id="sandboxed-trusted-flag-worker",
            sandbox_profile="sandboxed",
            trusted_worker=True,
        )


def test_sandboxed_oci_can_use_kata_when_host_selection_prefers_stronger_runtime() -> None:
    image = "example/worker@sha256:" + "c" * 64
    adapter = OCIAdapter(
        image,
        worker_id="stronger-sandboxed-worker",
        sandbox_profile="sandboxed",
        sandbox_runtime="kata",
    )

    assert adapter.sandbox_class == "sandboxed"
    assert adapter.sandbox_runtime == "kata"
    assert adapter.command[adapter.command.index("--runtime") + 1] == "kata"


def test_unknown_sandbox_class_fails_closed() -> None:
    with pytest.raises(ValueError, match="unsupported sandbox"):
        canonical_sandbox_class("runc")


def test_host_runtime_cannot_weaken_or_mismatch_the_policy_class() -> None:
    assert _normalize_sandbox_runtime(None, sandbox_profile="sandboxed") == "runsc"
    assert _normalize_sandbox_runtime(None, sandbox_profile="vm_isolated") == "kata"
    assert _normalize_sandbox_runtime("kata-qemu", sandbox_profile="vm_isolated") == "kata-qemu"
    with pytest.raises(ValueError, match="does not implement"):
        _normalize_sandbox_runtime("runc", sandbox_profile="sandboxed")
    with pytest.raises(ValueError, match="unsupported host sandbox runtime"):
        _normalize_sandbox_runtime("firecracker", sandbox_profile="vm_isolated")


def test_kata_can_satisfy_a_sandboxed_minimum_but_not_the_reverse() -> None:
    assert runtime_implements_sandbox("kata", "sandboxed") is True
    assert runtime_implements_sandbox("runsc", "vm_isolated") is False


def test_placement_rejects_a_vm_policy_on_a_host_without_kata() -> None:
    decision = evaluate_host(
        WorkerHostSnapshot(
            host_id="runsc-only",
            status="READY",
            sandbox_profiles=frozenset({"vm_isolated"}),
            sandbox_runtimes=frozenset({"runsc"}),
            isolation_modes=frozenset({"gvisor"}),
            capacity=HostCapacity(slots_total=1),
        ),
        WorkerPlacementRequest(
            worker_id="vm-worker",
            required_sandbox_profile="vm_isolated",
        ),
    )
    assert decision.eligible is False
    assert "sandbox_runtime_unsupported" in decision.reason_codes


def test_placement_accepts_a_discovered_kata_runtime_name() -> None:
    decision = evaluate_host(
        WorkerHostSnapshot(
            host_id="kata-qemu-host",
            status="READY",
            sandbox_profiles=frozenset({"vm_isolated"}),
            sandbox_runtimes=frozenset({"kata-qemu"}),
            isolation_modes=frozenset({"kata"}),
            capacity=HostCapacity(slots_total=1),
        ),
        WorkerPlacementRequest(
            worker_id="vm-worker",
            required_sandbox_profile="vm_isolated",
        ),
    )

    assert decision.eligible is True

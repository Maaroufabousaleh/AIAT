"""Tests for the canonical sandbox policy and host-runtime selection."""

from __future__ import annotations

import pytest

from mas_core.protocols.worker_manifest import WorkerManifest
from mas_core.sandbox_policy import (
    HARDENED_SANDBOX_CLASSES,
    canonical_sandbox_class,
    is_hardened_sandbox,
    required_runtime_for_sandbox,
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

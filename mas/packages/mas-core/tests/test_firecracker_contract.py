"""Tests for the fail-closed Firecracker worker launch contract."""

from __future__ import annotations

import pytest

from mas_core.worker_registry.firecracker import FirecrackerLaunchSpec
from mas_core.worker_registry.runtime_adapters import (
    FirecrackerAdapter,
    OCIAdapter,
    adapter_for_transport,
)

KERNEL_SHA = "a" * 64
ROOTFS_SHA = "b" * 64


def _spec(**overrides: object) -> FirecrackerLaunchSpec:
    values: dict[str, object] = {
        "launcher": "aiat-firecracker-launcher",
        "kernel_path": "/var/lib/aiat/firecracker/kernel-v1",
        "kernel_sha256": KERNEL_SHA,
        "rootfs_path": "/var/lib/aiat/firecracker/rootfs-v1.ext4",
        "rootfs_sha256": ROOTFS_SHA,
        "artifact_dir": "/var/lib/aiat/firecracker/artifacts/run-v1",
        "secret_refs": ("gateway/worker-token",),
    }
    values.update(overrides)
    return FirecrackerLaunchSpec(**values)


def test_firecracker_spec_builds_bounded_argv_without_secret_values() -> None:
    spec = _spec()

    command = spec.argv()

    assert command[0] == "aiat-firecracker-launcher"
    assert "--network-mode" in command
    assert command[command.index("--network-mode") + 1] == "egress-deny-all"
    assert "--rootfs-read-only" in command
    assert command[command.index("--cleanup") + 1] == "true"
    assert "--output-limit-bytes" in command
    assert "--wall-clock-seconds" in command
    assert "--secret-ref" in command
    assert "gateway/worker-token" in command
    assert "actual-secret-value" not in command
    assert spec.public_projection()["secret_ref_count"] == 1


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("kernel_sha256", "not-a-digest", "SHA-256"),
        ("rootfs_path", "relative/rootfs.ext4", "absolute"),
        ("artifact_dir", "/var/lib/aiat/../escape", "parent traversal"),
        ("network_mode", "unrestricted", "network_mode"),
        ("readonly_rootfs", False, "read-only"),
        ("cleanup", False, "cleanup"),
    ],
)
def test_firecracker_spec_rejects_unsafe_values(field: str, value: object, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        _spec(**{field: value})


def test_firecracker_spec_requires_allowlist_for_egress_mode() -> None:
    with pytest.raises(ValueError, match="allowlist"):
        _spec(network_mode="egress-allowlist")


def test_firecracker_adapter_and_oci_factory_use_the_explicit_launcher() -> None:
    config = {
        "image": "unused@sha256:" + "c" * 64,
        "sandbox_profile": "firecracker",
        "kernel_path": "/var/lib/aiat/firecracker/kernel-v1",
        "kernel_sha256": KERNEL_SHA,
        "rootfs_path": "/var/lib/aiat/firecracker/rootfs-v1.ext4",
        "rootfs_sha256": ROOTFS_SHA,
        "artifact_dir": "/var/lib/aiat/firecracker/artifacts/run-v1",
        "secret_refs": ["gateway/worker-token"],
    }

    adapter = adapter_for_transport("oci", worker_id="firecracker-worker", config=config)

    assert isinstance(adapter, FirecrackerAdapter)
    assert adapter.sandbox_profile == "firecracker"
    assert adapter.command[0] == "aiat-firecracker-launcher"
    assert "--runtime" not in adapter.command


def test_direct_oci_adapter_does_not_silently_fallback_to_firecracker() -> None:
    with pytest.raises(ValueError, match="certified Firecracker launcher"):
        OCIAdapter(
            "example/worker@sha256:" + "d" * 64,
            worker_id="oci-worker",
            sandbox_profile="firecracker",
        )

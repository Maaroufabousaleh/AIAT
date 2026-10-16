from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING

import pytest

from mas_core.execution_policy import (
    HostExecutionCapabilities,
    WorkloadExecutionPolicy,
    resolve_execution_target,
)
from mas_core.protocols.worker_manifest import WorkerManifest
from mas_core.worker_registry.placement import (
    HostCapacity,
    WorkerHostSnapshot,
    WorkerPlacementRequest,
    evaluate_host,
)
from mas_core.worker_registry.runtime_adapters import WasmtimeAdapter, adapter_for_transport

if TYPE_CHECKING:
    from pathlib import Path


def _host(**overrides: object) -> HostExecutionCapabilities:
    values: dict[str, object] = {
        "runtimes": {"runc", "runsc"},
        "workload_formats": {"oci", "linux"},
        "kvm_available": False,
        "gvisor_platform": "systrap",
    }
    values.update(overrides)
    return HostExecutionCapabilities(**values)  # type: ignore[arg-type]


def test_wasm_uses_wasmtime_when_available_even_on_a_kvm_host() -> None:
    target = resolve_execution_target(
        WorkloadExecutionPolicy(trust="untrusted", formats=("wasm",)),
        _host(
            runtimes={"runc", "runsc", "kata", "wasmtime"},
            workload_formats={"oci", "linux", "wasm"},
            kvm_available=True,
            wasmtime_available=True,
        ),
    )

    assert target.available is True
    assert target.runtime == "wasmtime"
    assert target.sandbox_class == "sandboxed"


def test_trusted_wasm_still_requires_a_wasm_capable_runtime() -> None:
    target = resolve_execution_target(
        WorkloadExecutionPolicy(trust="trusted", formats=("wasm",)),
        _host(workload_formats={"wasm"}, wasmtime_available=True),
    )

    assert target.available is True
    assert target.runtime == "wasmtime"
    assert target.sandbox_class == "sandboxed"


def test_vm_required_wasm_does_not_select_local_kata_without_wasm_adapter() -> None:
    target = resolve_execution_target(
        WorkloadExecutionPolicy(trust="hostile", formats=("wasm",)),
        _host(
            runtimes={"runc", "runsc", "kata", "wasmtime"},
            workload_formats={"oci", "linux", "wasm"},
            kvm_available=True,
            wasmtime_available=True,
        ),
    )

    assert target.available is False
    assert target.runtime is None
    assert target.reason_code == "vm_isolation_unavailable"


def test_vm_required_wasm_can_select_an_explicit_remote_vm() -> None:
    target = resolve_execution_target(
        WorkloadExecutionPolicy(trust="hostile", formats=("wasm",)),
        _host(
            runtimes={"runc", "runsc", "remote_vm"},
            workload_formats={"wasm"},
            remote_vm_available=True,
        ),
    )

    assert target.available is True
    assert target.runtime == "remote_vm"
    assert target.sandbox_class == "vm_isolated"


def test_host_format_capabilities_are_enforced() -> None:
    target = resolve_execution_target(
        WorkloadExecutionPolicy(trust="untrusted", formats=("wasm",)),
        _host(workload_formats={"oci", "linux"}),
    )

    assert target.available is False
    assert target.reason_code == "workload_format_unavailable"


def test_untrusted_oci_prefers_kata_when_kvm_and_kata_are_certified() -> None:
    target = resolve_execution_target(
        WorkloadExecutionPolicy(trust="untrusted", formats=("oci",)),
        _host(runtimes={"runc", "runsc", "kata"}, kvm_available=True),
    )

    assert target.runtime == "kata"
    assert target.sandbox_class == "vm_isolated"


def test_untrusted_oci_uses_gvisor_without_kvm() -> None:
    target = resolve_execution_target(
        WorkloadExecutionPolicy(trust="untrusted", formats=("oci",)),
        _host(),
    )

    assert target.runtime == "runsc"
    assert target.sandbox_class == "sandboxed"
    assert target.gvisor_platform == "systrap"


def test_gvisor_incompatible_workload_promotes_to_kata_when_available() -> None:
    target = resolve_execution_target(
        WorkloadExecutionPolicy(
            trust="untrusted",
            formats=("oci",),
            gvisor_compatible=False,
        ),
        _host(runtimes={"runc", "runsc", "kata"}, kvm_available=True),
    )

    assert target.available is True
    assert target.runtime == "kata"
    assert target.sandbox_class == "vm_isolated"


def test_hostile_workload_uses_explicit_remote_vm_when_local_kata_is_unavailable() -> None:
    target = resolve_execution_target(
        WorkloadExecutionPolicy(trust="hostile", formats=("oci",)),
        _host(remote_vm_available=True, runtimes={"runc", "runsc", "remote_vm"}),
    )

    assert target.runtime == "remote_vm"
    assert target.sandbox_class == "vm_isolated"


def test_vm_required_workload_fails_closed_without_kata_or_remote_vm() -> None:
    target = resolve_execution_target(
        WorkloadExecutionPolicy(
            trust="untrusted",
            formats=("oci",),
            minimum_isolation="vm_isolated",
        ),
        _host(),
    )

    assert target.available is False
    assert target.runtime is None
    assert target.reason_code == "vm_isolation_unavailable"


def test_gvisor_incompatible_workload_does_not_downgrade_to_runsc_or_runc() -> None:
    target = resolve_execution_target(
        WorkloadExecutionPolicy(
            trust="untrusted",
            formats=("oci",),
            gvisor_compatible=False,
        ),
        _host(),
    )

    assert target.available is False
    assert target.runtime is None
    assert target.reason_code == "gvisor_incompatible_without_vm"


def test_trusted_policy_resolves_to_runc_only() -> None:
    target = resolve_execution_target(
        WorkloadExecutionPolicy(trust="trusted"),
        _host(),
    )

    assert target.runtime == "runc"
    assert target.sandbox_class == "trusted"


def test_explicit_sandbox_floor_never_resolves_to_runc() -> None:
    target = resolve_execution_target(
        WorkloadExecutionPolicy(trust="trusted", minimum_isolation="sandboxed"),
        _host(),
    )

    assert target.runtime == "runsc"
    assert target.sandbox_class == "sandboxed"


def test_external_policy_cannot_select_trusted_runtime() -> None:
    target = resolve_execution_target(
        WorkloadExecutionPolicy(trust="untrusted", allowed_runtimes=("runc",)),
        _host(),
    )

    assert target.available is False
    assert target.runtime is None


def test_vmm_selection_is_not_a_worker_policy_field() -> None:
    with pytest.raises(ValueError, match="Extra inputs"):
        WorkloadExecutionPolicy(
            trust="untrusted",
            formats=("oci",),
            vmm="qemu",  # type: ignore[call-arg]
        )


def test_host_capability_mapping_accepts_runtime_lists_without_losing_kata() -> None:
    capabilities = HostExecutionCapabilities.from_mapping(
        {
            "execution_runtimes": ["runc", "runsc", "kata"],
            "workload_formats": ["oci", "linux"],
            "kvm_available": True,
        }
    )

    assert capabilities.runtimes == frozenset({"runc", "runsc", "kata"})
    assert capabilities.kvm_available is True
    assert "kata" in capabilities.runtimes


def test_kvm_alone_does_not_advertise_kata() -> None:
    capabilities = HostExecutionCapabilities.from_mapping(
        {
            "execution_runtimes": ["runc", "runsc"],
            "kvm_available": True,
        }
    )

    assert capabilities.kvm_available is True
    assert "kata" not in capabilities.runtimes


def test_kvm_and_legacy_vm_profile_do_not_fabricate_kata() -> None:
    capabilities = HostExecutionCapabilities.from_mapping(
        {
            "sandbox_profile": "vm_isolated",
            "sandbox_runtime": "kata",
            "execution_capabilities_advertised": False,
            "kvm_available": True,
        }
    )

    assert "kata" not in capabilities.runtimes


def test_legacy_kata_projection_does_not_turn_kvm_into_execution_capability() -> None:
    capabilities = HostExecutionCapabilities.from_mapping(
        {
            "sandbox_profile": "vm_isolated",
            "sandbox_runtime": "kata",
            "execution_capabilities_advertised": False,
            "kvm_available": True,
        }
    )

    target = resolve_execution_target(
        WorkloadExecutionPolicy(trust="untrusted", formats=("oci",)),
        capabilities,
    )

    assert "kata" not in capabilities.runtimes
    assert target.available is False
    assert target.reason_code == "execution_runtime_unavailable"


def test_manifest_keeps_legacy_alias_readable_and_derives_external_policy() -> None:
    manifest = WorkerManifest.model_validate(
        {
            "metadata": {
                "id": "legacy-worker",
                "name": "Legacy worker",
                "source_repo": "https://example.invalid/worker",
            },
            "runtime_tier": "external",
            "sandbox": {"profile": "gvisor"},
        }
    )

    assert manifest.sandbox.profile == "gvisor"
    assert manifest.effective_execution_policy.trust == "untrusted"
    assert manifest.effective_execution_policy.minimum_isolation == "sandboxed"


def test_placement_selects_only_hosts_with_the_required_execution_target() -> None:
    request = WorkerPlacementRequest(
        worker_id="worker-1",
        execution_policy=WorkloadExecutionPolicy(
            trust="hostile",
            formats=("oci",),
            remote_vm_allowed=False,
        ),
    )
    decision = evaluate_host(
        WorkerHostSnapshot(
            host_id="runsc-only",
            status="READY",
            sandbox_profiles=frozenset({"sandboxed"}),
            sandbox_runtimes=frozenset({"runsc"}),
            execution_runtimes=frozenset({"runc", "runsc"}),
            workload_formats=frozenset({"oci", "linux"}),
            capacity=HostCapacity(slots_total=1),
        ),
        request,
    )

    assert decision.eligible is False
    assert decision.execution_runtime is None
    assert "vm_isolation_unavailable" in decision.reason_codes


def test_placement_selects_advertised_kata_for_untrusted_oci() -> None:
    request = WorkerPlacementRequest(
        worker_id="worker-1",
        execution_policy=WorkloadExecutionPolicy(
            trust="untrusted",
            formats=("oci",),
        ),
    )
    decision = evaluate_host(
        WorkerHostSnapshot(
            host_id="kata-host",
            status="READY",
            execution_runtimes=frozenset({"runc", "runsc", "kata"}),
            workload_formats=frozenset({"oci", "linux"}),
            kvm_available=True,
            execution_capabilities_advertised=True,
            capacity=HostCapacity(slots_total=1),
        ),
        request,
    )

    assert decision.eligible is True
    assert decision.execution_runtime == "kata"
    assert decision.execution_sandbox_class == "vm_isolated"


def test_placement_does_not_use_legacy_vm_profile_as_kata_capability() -> None:
    request = WorkerPlacementRequest(
        worker_id="worker-1",
        execution_policy=WorkloadExecutionPolicy(trust="untrusted", formats=("oci",)),
    )
    decision = evaluate_host(
        WorkerHostSnapshot(
            host_id="legacy-vm-row",
            status="READY",
            sandbox_profiles=frozenset({"vm_isolated"}),
            sandbox_runtimes=frozenset({"kata"}),
            execution_runtimes=frozenset(),
            execution_capabilities_advertised=False,
            kvm_available=True,
            workload_formats=frozenset({"oci", "linux"}),
            capacity=HostCapacity(slots_total=1),
        ),
        request,
    )

    assert decision.eligible is False
    assert decision.execution_runtime is None


def test_wasmtime_adapter_requires_immutable_module(tmp_path: Path) -> None:
    path = tmp_path / "worker.wasm"
    path.write_bytes(b"wasm-fixture")
    digest = hashlib.sha256(b"wasm-fixture").hexdigest()
    adapter = WasmtimeAdapter(
        str(path),
        module_sha256=digest,
        worker_id="wasm-worker",
    )

    assert adapter.runtime_type == "wasmtime"
    assert adapter.command[:2] == ["wasmtime", "run"]
    assert adapter.command[-1] == str(path.resolve())

    with pytest.raises(ValueError, match="digest"):
        adapter_for_transport(
            "wasmtime",
            worker_id="wasm-worker",
            config={"module_path": str(path), "module_sha256": "0" * 64},
        )

"""AIAT workload-format and host-capability execution policy.

This module decides which already-certified runtime class may execute a
workload.  It is deliberately a policy/resolution layer, not an execution
backend abstraction.  Runtime adapters and the host scheduler remain the
execution authorities after a target has been selected.

The policy keeps three important distinctions explicit:

* WASM is a workload format and uses Wasmtime only when a WASM artifact is
  declared and the host advertises Wasmtime.
* OCI/Linux workloads use Kata when local KVM-backed Kata is available, and
  otherwise use gVisor when the workload is compatible with it.
* Hostile or VM-required workloads never downgrade to gVisor or runc.  They
  require Kata locally or an explicitly advertised remote VM capability.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Final, Literal

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, field_validator, model_validator

from mas_core.sandbox_policy import runtime_implements_sandbox

if TYPE_CHECKING:
    from collections.abc import Mapping

ExecutionTrust = Literal["trusted", "untrusted", "hostile"]
ExecutionFormat = Literal["oci", "linux", "wasm"]
ExecutionRuntime = Literal["runc", "wasmtime", "runsc", "kata", "remote_vm"]
MinimumIsolation = Literal["trusted", "sandboxed", "vm_isolated", "remote_vm"]

EXECUTION_RUNTIMES: Final[frozenset[str]] = frozenset(
    {"runc", "wasmtime", "runsc", "kata", "remote_vm"}
)
EXECUTION_FORMATS: Final[frozenset[str]] = frozenset({"oci", "linux", "wasm"})


class WorkloadExecutionPolicy(BaseModel):
    """Declarative workload requirements carried by a worker manifest."""

    model_config = ConfigDict(extra="forbid")

    trust: ExecutionTrust | None = None
    formats: tuple[ExecutionFormat, ...] = ("oci",)
    preferred_runtimes: tuple[ExecutionRuntime, ...] = Field(
        default=(),
        validation_alias=AliasChoices("preferred_runtimes", "preferred_runtime"),
    )
    allowed_runtimes: tuple[ExecutionRuntime, ...] = ()
    minimum_isolation: MinimumIsolation | None = None
    gvisor_compatible: bool = True
    remote_vm_allowed: bool = True
    network: Literal["none", "egress-allowlist", "egress-deny-all", "unrestricted"] = (
        "egress-allowlist"
    )
    filesystem: dict[str, str] = Field(default_factory=dict)

    @field_validator("formats", mode="before")
    @classmethod
    def normalize_formats(cls, value: Any) -> tuple[str, ...]:
        values = (value,) if isinstance(value, str) else tuple(value or ())
        normalized = tuple(str(item).strip().lower() for item in values if str(item).strip())
        if not normalized:
            raise ValueError("execution formats must not be empty")
        invalid = set(normalized) - EXECUTION_FORMATS
        if invalid:
            raise ValueError(f"unsupported execution format(s): {sorted(invalid)}")
        return tuple(dict.fromkeys(normalized))

    @field_validator("preferred_runtimes", "allowed_runtimes", mode="before")
    @classmethod
    def normalize_runtimes(cls, value: Any) -> tuple[str, ...]:
        values = (value,) if isinstance(value, str) else tuple(value or ())
        normalized = tuple(str(item).strip().lower() for item in values if str(item).strip())
        invalid = set(normalized) - EXECUTION_RUNTIMES
        if invalid:
            raise ValueError(f"unsupported execution runtime(s): {sorted(invalid)}")
        return tuple(dict.fromkeys(normalized))

    @field_validator("minimum_isolation", mode="before")
    @classmethod
    def normalize_minimum_isolation(cls, value: Any) -> Any:
        if value is None:
            return None
        normalized = str(value).strip().lower()
        return "vm_isolated" if normalized == "vm" else normalized

    @model_validator(mode="after")
    def validate_security_floor(self) -> WorkloadExecutionPolicy:
        minimum = self.minimum_isolation
        if self.trust == "hostile" and minimum not in {None, "vm_isolated", "remote_vm"}:
            raise ValueError("hostile workloads require vm_isolated or remote_vm isolation")
        if self.trust == "untrusted" and minimum == "trusted":
            raise ValueError("untrusted workloads cannot require trusted/runc execution")
        if self.trust == "trusted" and any(
            runtime not in {"runc", "wasmtime"} for runtime in self.allowed_runtimes
        ):
            raise ValueError("trusted workloads may only allow runc or explicitly selected Wasmtime")
        return self

    def effective_trust(self, *, external: bool = False) -> ExecutionTrust:
        """Resolve omitted trust without making external work trusted."""

        if self.trust is not None:
            return self.trust
        return "untrusted" if external else "trusted"

    def effective_minimum_isolation(self, *, external: bool = False) -> MinimumIsolation:
        """Return the security floor implied by trust and explicit policy."""

        if self.minimum_isolation is not None:
            return self.minimum_isolation
        trust = self.effective_trust(external=external)
        if trust == "hostile":
            return "vm_isolated"
        if trust == "untrusted":
            return "sandboxed"
        return "trusted"


class HostExecutionCapabilities(BaseModel):
    """Capabilities advertised by one worker host after certification."""

    model_config = ConfigDict(extra="ignore")

    runtimes: frozenset[str] = frozenset({"runc"})
    workload_formats: frozenset[str] = frozenset()
    kvm_available: bool = False
    wasmtime_available: bool = False
    remote_vm_available: bool = False
    gvisor_platform: str = "systrap"

    @field_validator("runtimes", "workload_formats", mode="before")
    @classmethod
    def normalize_sets(cls, value: Any) -> frozenset[str]:
        values = (value,) if isinstance(value, str) else tuple(value or ())
        return frozenset(str(item).strip().lower() for item in values if str(item).strip())

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any] | None) -> HostExecutionCapabilities:
        """Read host metadata without trusting it as proof of certification."""

        data = dict(value or {})
        if data.get("execution_capabilities_advertised") is False:
            # A registered legacy host may still expose sandbox_profile and
            # sandbox_runtime for compatibility.  Those fields are not
            # proof of a format-aware execution capability and must not make
            # KVM alone look like certified Kata.
            runtimes = data.get("execution_runtimes") or ()
        elif "execution_runtimes" in data:
            runtimes = data.get("execution_runtimes")
        elif "runtime_capabilities" in data:
            runtimes = data.get("runtime_capabilities")
        else:
            runtimes = data.get("sandbox_runtimes") or data.get("sandbox_runtime") or ()
        runtime_values = (runtimes,) if isinstance(runtimes, str) else tuple(runtimes or ())
        runtime_set = set(runtime_values)
        runtime_set = {str(item).strip().lower() for item in runtime_set if str(item).strip()}
        # KVM is a prerequisite, not proof that Kata runtime-rs is installed,
        # registered, configured, or guest-smoke certified. Keep those host
        # facts separate so placement cannot select Kata from /dev/kvm alone.
        if data.get("wasmtime_available"):
            runtime_set.add("wasmtime")
        if data.get("remote_vm_available"):
            runtime_set.add("remote_vm")
        runtime_set.add("runc")
        formats = data.get("workload_formats") or data.get("execution_formats") or ()
        format_values = (formats,) if isinstance(formats, str) else tuple(formats or ())
        format_set = set(format_values)
        format_set = {str(item).strip().lower() for item in format_set if str(item).strip()}
        if "wasmtime" in runtime_set:
            format_set.add("wasm")
        if runtime_set & {"runsc", "kata", "runc"}:
            format_set.update({"oci", "linux"})
        return cls(
            runtimes=frozenset(runtime_set),
            workload_formats=frozenset(format_set),
            kvm_available=bool(data.get("kvm_available", False)),
            wasmtime_available=bool(data.get("wasmtime_available", "wasmtime" in runtime_set)),
            remote_vm_available=bool(data.get("remote_vm_available", "remote_vm" in runtime_set)),
            gvisor_platform=str(data.get("gvisor_platform") or "systrap"),
        )


@dataclass(frozen=True, slots=True)
class ExecutionTarget:
    """The non-authoritative target selected for one host/workload pair."""

    runtime: str | None
    sandbox_class: str | None
    available: bool
    reason_code: str
    reason: str
    gvisor_platform: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "runtime": self.runtime,
            "sandbox_class": self.sandbox_class,
            "available": self.available,
            "reason_code": self.reason_code,
            "reason": self.reason,
            "gvisor_platform": self.gvisor_platform,
        }


def _runtime_available(runtime: str, host: HostExecutionCapabilities) -> bool:
    if runtime == "kata":
        return host.kvm_available and any(
            runtime_implements_sandbox(candidate, "vm_isolated")
            for candidate in host.runtimes
        )
    if runtime == "runsc":
        return "runsc" in host.runtimes
    if runtime == "wasmtime":
        return host.wasmtime_available or "wasmtime" in host.runtimes
    if runtime == "remote_vm":
        return host.remote_vm_available or "remote_vm" in host.runtimes
    if runtime == "runc":
        return "runc" in host.runtimes or "io.containerd.runc.v2" in host.runtimes
    return False


def _sandbox_class_for_runtime(runtime: str) -> str:
    if runtime == "runc":
        return "trusted"
    if runtime in {"runsc", "wasmtime"}:
        return "sandboxed"
    return "vm_isolated"


def _format_available(
    policy: WorkloadExecutionPolicy,
    host: HostExecutionCapabilities,
) -> bool:
    """Return whether the host advertises at least one requested format.

    An empty format set is retained as a legacy/unspecified host capability
    for existing registry rows. Once a host advertises formats, selection is
    strict so a WASM-only request cannot land on an OCI-only host.
    """

    if not host.workload_formats:
        return True
    return bool(set(policy.formats) & set(host.workload_formats))


def resolve_execution_target(
    policy: WorkloadExecutionPolicy | Mapping[str, Any],
    host: HostExecutionCapabilities | Mapping[str, Any],
    *,
    external: bool = True,
) -> ExecutionTarget:
    """Select a safe target without permitting a security downgrade."""

    execution_policy = (
        policy
        if isinstance(policy, WorkloadExecutionPolicy)
        else WorkloadExecutionPolicy.model_validate(policy)
    )
    capabilities = (
        host if isinstance(host, HostExecutionCapabilities) else HostExecutionCapabilities.from_mapping(host)
    )
    trust = execution_policy.effective_trust(external=external)
    minimum = execution_policy.effective_minimum_isolation(external=external)
    allowed = set(execution_policy.allowed_runtimes)

    if not _format_available(execution_policy, capabilities):
        return ExecutionTarget(
            runtime=None,
            sandbox_class=None,
            available=False,
            reason_code="workload_format_unavailable",
            reason=(
                "host does not advertise a format compatible with "
                f"{','.join(execution_policy.formats)}"
            ),
        )

    wasm_only = "wasm" in execution_policy.formats and not (
        {"oci", "linux"} & set(execution_policy.formats)
    )
    if wasm_only:
        # A Wasmtime module cannot be handed to an OCI runtime merely because
        # the host has Kata/runsc. VM-required WASM may use only an explicitly
        # advertised remote VM that also accepts the WASM format; the local
        # Kata adapter is for OCI/Linux guests and does not launch WASM modules.
        if trust == "hostile" or minimum in {"vm_isolated", "remote_vm"}:
            candidates = ["remote_vm"]
        else:
            candidates = ["wasmtime", "remote_vm"]
    elif minimum == "remote_vm":
        candidates = ["remote_vm"]
    elif trust == "hostile" or minimum == "vm_isolated":
        candidates = ["kata", "remote_vm"]
    elif minimum == "sandboxed":
        # A declared sandbox floor applies even to an AIAT-owned worker that
        # elects to run outside the trusted control-plane process.  runc is
        # never a substitute for that floor.
        if "wasm" in execution_policy.formats:
            candidates = ["wasmtime"]
            if "oci" in execution_policy.formats or "linux" in execution_policy.formats:
                candidates.extend(["kata", "runsc", "remote_vm"])
        elif not execution_policy.gvisor_compatible:
            candidates = ["kata", "remote_vm"]
        else:
            candidates = ["kata", "runsc", "remote_vm"]
    elif trust == "trusted":
        candidates = ["runc"]
    elif "wasm" in execution_policy.formats and trust != "hostile":
        candidates = ["wasmtime"]
        if "oci" in execution_policy.formats or "linux" in execution_policy.formats:
            candidates.extend(["kata", "runsc", "remote_vm"])
    elif not execution_policy.gvisor_compatible:
        candidates = ["kata", "remote_vm"]
    else:
        # Kata is preferred for untrusted OCI/Linux work when KVM is actually
        # available; runsc is the compatibility path on non-KVM hosts.
        candidates = ["kata", "runsc", "remote_vm"]

    if not execution_policy.remote_vm_allowed:
        candidates = [candidate for candidate in candidates if candidate != "remote_vm"]
    if allowed:
        candidates = [candidate for candidate in candidates if candidate in allowed]

    # A declared preference can reorder candidates, but cannot introduce a
    # weaker runtime or move runsc ahead of available Kata for normal OCI work.
    preferred = [runtime for runtime in execution_policy.preferred_runtimes if runtime in candidates]
    ordered: list[str] = []
    if "kata" in candidates and capabilities.kvm_available:
        ordered.append("kata")
    ordered.extend(runtime for runtime in preferred if runtime not in ordered)
    ordered.extend(runtime for runtime in candidates if runtime not in ordered)

    for runtime in ordered:
        if _runtime_available(runtime, capabilities):
            return ExecutionTarget(
                runtime=runtime,
                sandbox_class=_sandbox_class_for_runtime(runtime),
                available=True,
                reason_code="execution_target_selected",
                reason=f"selected {runtime} for {trust} {','.join(execution_policy.formats)} workload",
                gvisor_platform=capabilities.gvisor_platform if runtime == "runsc" else None,
            )

    if trust == "hostile" or minimum in {"vm_isolated", "remote_vm"}:
        reason_code = "vm_isolation_unavailable"
        reason = "hostile or VM-required workload has no local Kata or remote VM target"
    elif not execution_policy.gvisor_compatible:
        reason_code = "gvisor_incompatible_without_vm"
        reason = "workload is incompatible with gVisor and no Kata/remote VM target is available"
    elif wasm_only:
        reason_code = "wasmtime_unavailable"
        reason = "WASM workload has no available Wasmtime host capability"
    else:
        reason_code = "execution_runtime_unavailable"
        reason = "no certified runtime satisfies the workload policy on this host"
    return ExecutionTarget(
        runtime=None,
        sandbox_class=None,
        available=False,
        reason_code=reason_code,
        reason=reason,
    )


__all__ = [
    "EXECUTION_FORMATS",
    "EXECUTION_RUNTIMES",
    "ExecutionFormat",
    "ExecutionRuntime",
    "ExecutionTarget",
    "ExecutionTrust",
    "HostExecutionCapabilities",
    "MinimumIsolation",
    "WorkloadExecutionPolicy",
    "resolve_execution_target",
]

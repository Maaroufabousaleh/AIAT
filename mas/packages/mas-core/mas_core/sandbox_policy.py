"""AIAT sandbox policy classes and compatibility aliases.

The policy class is intentionally separate from the host runtime. AIAT
authorizes a security class; a certified worker host selects the concrete OCI
runtime or VMM implementation for that class.

Canonical classes are ``trusted``, ``sandboxed``, and ``vm_isolated``. The
historical names remain accepted during migration. ``restricted`` was never a
runtime guarantee, so it maps to ``trusted`` rather than being treated as a
hardened sandbox.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Final, Literal

CanonicalSandboxClass = Literal["trusted", "sandboxed", "vm_isolated"]
SandboxProfileName = Literal[
    "trusted",
    "sandboxed",
    "vm_isolated",
    "standard",
    "restricted",
    "gvisor",
    "firecracker",
]

CANONICAL_SANDBOX_CLASSES: Final[frozenset[str]] = frozenset(
    {"trusted", "sandboxed", "vm_isolated"}
)
LEGACY_SANDBOX_PROFILES: Final[frozenset[str]] = frozenset(
    {"standard", "restricted", "gvisor", "firecracker"}
)
SUPPORTED_SANDBOX_PROFILES: Final[frozenset[str]] = (
    CANONICAL_SANDBOX_CLASSES | LEGACY_SANDBOX_PROFILES
)
HARDENED_SANDBOX_CLASSES: Final[frozenset[str]] = frozenset(
    {"sandboxed", "vm_isolated"}
)
SANDBOX_PROFILE_ALIASES: Final[dict[str, CanonicalSandboxClass]] = {
    "trusted": "trusted",
    "sandboxed": "sandboxed",
    "vm_isolated": "vm_isolated",
    "standard": "trusted",
    "restricted": "trusted",
    "gvisor": "sandboxed",
    "firecracker": "vm_isolated",
}
SANDBOX_CLASS_RUNTIME: Final[dict[str, str]] = {
    "trusted": "runc",
    "sandboxed": "runsc",
    "vm_isolated": "kata",
}


def canonical_sandbox_class(value: object, *, default: str | None = None) -> CanonicalSandboxClass:
    """Return the canonical policy class for a profile or fail closed."""

    raw = str(value or "").strip().lower()
    if not raw:
        raw = str(default or "").strip().lower()
    try:
        return SANDBOX_PROFILE_ALIASES[raw]
    except KeyError as exc:
        raise ValueError(f"unsupported sandbox profile/class: {value!r}") from exc


def is_hardened_sandbox(value: object) -> bool:
    """Whether a profile resolves to an external-worker isolation class."""

    try:
        return canonical_sandbox_class(value) in HARDENED_SANDBOX_CLASSES
    except ValueError:
        return False


def required_runtime_for_sandbox(value: object) -> str:
    """Return the host runtime required by a canonical policy class."""

    return SANDBOX_CLASS_RUNTIME[canonical_sandbox_class(value)]


def runtime_implements_sandbox(runtime: object, value: object) -> bool:
    """Return whether a concrete host runtime implements a policy class.

    Docker may register Kata under a concrete runtime name such as
    ``kata-qemu``.  The policy class remains ``vm_isolated`` and the logical
    required runtime remains ``kata``; this predicate keeps those names
    distinct while allowing the discovered runtime to flow through host
    registration, placement, and OCI dispatch.  Kata also satisfies a
    ``sandboxed`` minimum because it is stronger than the normal runsc target;
    a stronger runtime may satisfy a floor, but a weaker runtime may not.
    """

    try:
        sandbox_class = canonical_sandbox_class(value)
    except ValueError:
        return False
    runtime_name = str(runtime or "").strip().lower()
    if sandbox_class == "vm_isolated":
        return runtime_name == "kata" or (
            runtime_name.startswith("kata-") and len(runtime_name) > len("kata-")
        )
    if sandbox_class == "sandboxed":
        # Kata is a stronger host implementation than the normal runsc
        # target.  A workload whose minimum floor is sandboxed may therefore
        # be placed on Kata when the format-aware host resolver prefers it;
        # the inverse downgrade (runsc/runc for vm_isolated) is never allowed.
        return runtime_name == "runsc" or runtime_name == "kata" or (
            runtime_name.startswith("kata-") and len(runtime_name) > len("kata-")
        )
    return runtime_name == SANDBOX_CLASS_RUNTIME[sandbox_class]


class WorkerIsolationPolicyError(ValueError):
    """A worker record contains an invalid sandbox or execution policy."""

    def __init__(self, reason_code: str) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code


def worker_requires_hardened_isolation(worker: Mapping[str, Any]) -> bool:
    """Resolve the isolation floor used by host and API dispatch boundaries.

    Source-backed and explicitly external workers remain untrusted even when
    older rows omit or contradict their sandbox declaration. Invalid local
    policy metadata fails closed with a stable reason code.
    """

    if not isinstance(worker, Mapping):
        raise WorkerIsolationPolicyError("worker_identity_invalid")

    raw_adapter_config = worker.get("adapter_config")
    if raw_adapter_config is None:
        adapter_config: Mapping[str, Any] = {}
    elif isinstance(raw_adapter_config, Mapping):
        adapter_config = raw_adapter_config
    else:
        raise WorkerIsolationPolicyError("worker_execution_policy_invalid")
    raw_legacy_manifest = adapter_config.get("legacy_manifest")
    if raw_legacy_manifest is None:
        legacy_manifest: Mapping[str, Any] = {}
    elif isinstance(raw_legacy_manifest, Mapping):
        legacy_manifest = raw_legacy_manifest
    else:
        raise WorkerIsolationPolicyError("worker_execution_policy_invalid")
    source_repo = str(worker.get("source_repo") or "").strip()
    runtime_tiers = {
        str(value or "").strip().lower()
        for value in (
            worker.get("runtime_tier"),
            adapter_config.get("runtime_tier"),
            legacy_manifest.get("runtime_tier"),
        )
    }
    # Treat conflicting runtime metadata conservatively: any persisted or
    # manifest-backed external declaration makes the worker untrusted.
    external = "external" in runtime_tiers or source_repo not in {"", "local"}
    try:
        sandbox_class = canonical_sandbox_class(
            str(worker.get("sandbox_profile") or "trusted")
        )
    except ValueError as exc:
        raise WorkerIsolationPolicyError("worker_sandbox_profile_invalid") from exc
    raw_policy = adapter_config.get("execution_policy")
    if raw_policy is None:
        from mas_core.execution_policy import WorkloadExecutionPolicy

        policy = WorkloadExecutionPolicy()
    else:
        if not isinstance(raw_policy, Mapping):
            raise WorkerIsolationPolicyError("worker_execution_policy_invalid")
        try:
            from mas_core.execution_policy import WorkloadExecutionPolicy

            policy = WorkloadExecutionPolicy.model_validate(raw_policy)
        except (TypeError, ValueError) as exc:
            raise WorkerIsolationPolicyError("worker_execution_policy_invalid") from exc
    return (
        external
        or sandbox_class != "trusted"
        or policy.effective_minimum_isolation(external=external) != "trusted"
    )


__all__ = [
    "CANONICAL_SANDBOX_CLASSES",
    "CanonicalSandboxClass",
    "HARDENED_SANDBOX_CLASSES",
    "LEGACY_SANDBOX_PROFILES",
    "SANDBOX_CLASS_RUNTIME",
    "SANDBOX_PROFILE_ALIASES",
    "SUPPORTED_SANDBOX_PROFILES",
    "SandboxProfileName",
    "WorkerIsolationPolicyError",
    "canonical_sandbox_class",
    "is_hardened_sandbox",
    "required_runtime_for_sandbox",
    "runtime_implements_sandbox",
    "worker_requires_hardened_isolation",
]

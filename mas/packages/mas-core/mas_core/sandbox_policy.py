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

from typing import Final, Literal

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
    registration, placement, and OCI dispatch.
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
    return runtime_name == SANDBOX_CLASS_RUNTIME[sandbox_class]


__all__ = [
    "CANONICAL_SANDBOX_CLASSES",
    "CanonicalSandboxClass",
    "HARDENED_SANDBOX_CLASSES",
    "LEGACY_SANDBOX_PROFILES",
    "SANDBOX_CLASS_RUNTIME",
    "SANDBOX_PROFILE_ALIASES",
    "SUPPORTED_SANDBOX_PROFILES",
    "SandboxProfileName",
    "canonical_sandbox_class",
    "is_hardened_sandbox",
    "required_runtime_for_sandbox",
    "runtime_implements_sandbox",
]

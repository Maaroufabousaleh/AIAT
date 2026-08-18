"""AIAT-owned Firecracker launch contract.

This module builds an argv-only launcher request for a certified Firecracker
wrapper. It deliberately does not invoke Firecracker itself: the wrapper is an
untrusted execution boundary and must be supplied by a host-certified profile.
The contract keeps kernel/rootfs provenance, resource limits, network policy,
opaque secret references, artifact output, and cleanup explicit before a
worker adapter can start.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Literal

NETWORK_MODES = frozenset({"egress-allowlist", "egress-deny-all"})
_SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")
_REF_RE = re.compile(r"^[A-Za-z0-9_./:-]+$")


def _validate_arg(value: str, *, field: str) -> str:
    normalized = str(value).strip()
    if not normalized:
        raise ValueError(f"{field} must not be blank")
    if any(char.isspace() or ord(char) < 32 for char in normalized):
        raise ValueError(f"{field} must not contain whitespace or control characters")
    return normalized


def _validate_absolute_path(value: str, *, field: str) -> str:
    normalized = _validate_arg(value, field=field)
    path = PurePosixPath(normalized)
    if not path.is_absolute():
        raise ValueError(f"{field} must be an absolute host path")
    if ".." in path.parts:
        raise ValueError(f"{field} must not contain parent traversal")
    return normalized


def _validate_digest(value: str, *, field: str) -> str:
    normalized = _validate_arg(value, field=field).lower()
    if not _SHA256_RE.fullmatch(normalized):
        raise ValueError(f"{field} must be a 64-character SHA-256 digest")
    return normalized


@dataclass(frozen=True, slots=True)
class FirecrackerLaunchSpec:
    """Validated, payload-free launch inputs for a Firecracker wrapper."""

    launcher: str
    kernel_path: str
    kernel_sha256: str
    rootfs_path: str
    rootfs_sha256: str
    artifact_dir: str
    network_mode: Literal["egress-allowlist", "egress-deny-all"] = "egress-deny-all"
    egress_allowlist: tuple[str, ...] = ()
    vcpu_count: int = 1
    memory_mib: int = 512
    pids_limit: int = 256
    disk_limit_mb: int = 1024
    output_limit_bytes: int = 4 * 1024 * 1024
    wall_clock_seconds: int = 300
    secret_refs: tuple[str, ...] = ()
    readonly_rootfs: bool = True
    cleanup: bool = True

    def __post_init__(self) -> None:
        object.__setattr__(self, "launcher", _validate_arg(self.launcher, field="launcher"))
        object.__setattr__(
            self,
            "kernel_path",
            _validate_absolute_path(self.kernel_path, field="kernel_path"),
        )
        object.__setattr__(
            self,
            "rootfs_path",
            _validate_absolute_path(self.rootfs_path, field="rootfs_path"),
        )
        object.__setattr__(
            self,
            "artifact_dir",
            _validate_absolute_path(self.artifact_dir, field="artifact_dir"),
        )
        object.__setattr__(
            self,
            "kernel_sha256",
            _validate_digest(self.kernel_sha256, field="kernel_sha256"),
        )
        object.__setattr__(
            self,
            "rootfs_sha256",
            _validate_digest(self.rootfs_sha256, field="rootfs_sha256"),
        )
        if self.network_mode not in NETWORK_MODES:
            raise ValueError("Firecracker network_mode must be egress-allowlist or egress-deny-all")
        if self.network_mode == "egress-deny-all" and self.egress_allowlist:
            raise ValueError("egress-deny-all cannot carry an egress allowlist")
        if self.network_mode == "egress-allowlist" and not self.egress_allowlist:
            raise ValueError("egress-allowlist requires at least one destination")
        normalized_egress = tuple(
            _validate_arg(destination, field="egress_allowlist entry")
            for destination in self.egress_allowlist
        )
        object.__setattr__(self, "egress_allowlist", normalized_egress)
        if not 1 <= int(self.vcpu_count) <= 16:
            raise ValueError("vcpu_count must be between 1 and 16")
        if not 64 <= int(self.memory_mib) <= 65_536:
            raise ValueError("memory_mib must be between 64 and 65536")
        if not 16 <= int(self.pids_limit) <= 65_536:
            raise ValueError("pids_limit must be between 16 and 65536")
        if not 16 <= int(self.disk_limit_mb) <= 1_048_576:
            raise ValueError("disk_limit_mb must be between 16 and 1048576")
        if not 1_024 <= int(self.output_limit_bytes) <= 1_073_741_824:
            raise ValueError("output_limit_bytes must be between 1024 and 1073741824")
        if not 1 <= int(self.wall_clock_seconds) <= 86_400:
            raise ValueError("wall_clock_seconds must be between 1 and 86400")
        normalized_refs = tuple(
            _validate_arg(reference, field="secret_refs entry") for reference in self.secret_refs
        )
        if any(not _REF_RE.fullmatch(reference) for reference in normalized_refs):
            raise ValueError("secret_refs entries must be opaque names without secret values")
        object.__setattr__(self, "secret_refs", normalized_refs)
        if not self.readonly_rootfs:
            raise ValueError("Firecracker rootfs must be read-only")
        if not self.cleanup:
            raise ValueError("Firecracker launchers must enable cleanup")

    def argv(self) -> list[str]:
        """Return an argv-only launcher command with no secret values."""

        command = [
            self.launcher,
            "--kernel",
            self.kernel_path,
            "--kernel-sha256",
            self.kernel_sha256,
            "--rootfs",
            self.rootfs_path,
            "--rootfs-sha256",
            self.rootfs_sha256,
            "--artifact-dir",
            self.artifact_dir,
            "--network-mode",
            self.network_mode,
            "--vcpu-count",
            str(self.vcpu_count),
            "--memory-mib",
            str(self.memory_mib),
            "--pids-limit",
            str(self.pids_limit),
            "--disk-limit-mb",
            str(self.disk_limit_mb),
            "--output-limit-bytes",
            str(self.output_limit_bytes),
            "--wall-clock-seconds",
            str(self.wall_clock_seconds),
            "--rootfs-read-only",
            "true",
            "--cleanup",
            "true",
        ]
        for destination in self.egress_allowlist:
            command.extend(("--egress-allowlist", destination))
        for reference in self.secret_refs:
            command.extend(("--secret-ref", reference))
        return command

    def public_projection(self) -> dict[str, object]:
        """Return safe scalar metadata for evidence and operator read models."""

        return {
            "launcher": self.launcher,
            "kernel_path": self.kernel_path,
            "kernel_sha256": self.kernel_sha256,
            "rootfs_path": self.rootfs_path,
            "rootfs_sha256": self.rootfs_sha256,
            "artifact_dir": self.artifact_dir,
            "network_mode": self.network_mode,
            "egress_allowlist": list(self.egress_allowlist),
            "vcpu_count": self.vcpu_count,
            "memory_mib": self.memory_mib,
            "pids_limit": self.pids_limit,
            "disk_limit_mb": self.disk_limit_mb,
            "output_limit_bytes": self.output_limit_bytes,
            "wall_clock_seconds": self.wall_clock_seconds,
            "secret_ref_count": len(self.secret_refs),
            "readonly_rootfs": self.readonly_rootfs,
            "cleanup": self.cleanup,
        }

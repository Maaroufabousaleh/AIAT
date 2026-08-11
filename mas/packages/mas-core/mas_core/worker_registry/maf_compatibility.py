"""Fail-closed compatibility preflight for Microsoft Agent Framework.

The framework is optional and remains outside the default runtime image.  This
module records the exact compatibility decision needed before activation; it
does not install packages, contact providers, or treat licence metadata as an
operational gate.
"""

from __future__ import annotations

import importlib
import importlib.metadata
import re
from dataclasses import dataclass
from typing import Any

MAF_COMPATIBILITY_SCHEMA = "aiat.microsoft-agent-framework-compatibility.v1"
MAF_DISTRIBUTION = "agent-framework"
MAF_IMPORT = "agent_framework"
MAF_LOCKED_VERSION = "1.13.0"
MCP_DISTRIBUTION = "mcp"
MCP_VERSION_SPECIFIER = ">=1.27,<2"


@dataclass(frozen=True, slots=True)
class MAFCompatibilityReport:
    """Secret-free result of the optional MAF/MCP compatibility preflight."""

    status: str
    package_version: str | None
    mcp_version: str | None
    blockers: tuple[str, ...] = ()
    notices: tuple[str, ...] = ()

    @property
    def ready(self) -> bool:
        return self.status == "ready" and not self.blockers

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": MAF_COMPATIBILITY_SCHEMA,
            "status": self.status,
            "ready": self.ready,
            "distribution": MAF_DISTRIBUTION,
            "import": MAF_IMPORT,
            "locked_version": MAF_LOCKED_VERSION,
            "mcp_distribution": MCP_DISTRIBUTION,
            "mcp_version_specifier": MCP_VERSION_SPECIFIER,
            "package_version": self.package_version,
            "mcp_version": self.mcp_version,
            "blockers": list(self.blockers),
            "notices": list(self.notices),
            "licence_metadata_is_gate": False,
        }


def _version_tuple(value: str | None) -> tuple[int, int, int] | None:
    if not value:
        return None
    match = re.match(r"^(\d+)\.(\d+)(?:\.(\d+))?", str(value).strip())
    if match is None:
        return None
    return tuple(int(part or 0) for part in match.groups())  # type: ignore[return-value]


def _mcp_version_satisfies(value: str | None) -> bool:
    parsed = _version_tuple(value)
    if parsed is None:
        return False
    # Equivalent to the locked >=1.27,<2 contract without adding a runtime
    # dependency on a version-specifier package to mas-core.
    return (1, 27, 0) <= parsed < (2, 0, 0)


def evaluate_microsoft_agent_framework_compatibility(
    *,
    module: Any | None = None,
    package_version: str | None = None,
    mcp_version: str | None = None,
) -> MAFCompatibilityReport:
    """Inspect the optional package and MCP version without executing a task."""

    blockers: list[str] = []
    notices: list[str] = []
    if module is None:
        try:
            module = importlib.import_module(MAF_IMPORT)
        except ImportError:
            blockers.append(f"{MAF_DISTRIBUTION} is not installed")

    if package_version is None:
        try:
            package_version = importlib.metadata.version(MAF_DISTRIBUTION)
        except importlib.metadata.PackageNotFoundError:
            package_version = None
    if mcp_version is None:
        try:
            mcp_version = importlib.metadata.version(MCP_DISTRIBUTION)
        except importlib.metadata.PackageNotFoundError:
            mcp_version = None

    if package_version != MAF_LOCKED_VERSION:
        blockers.append(
            f"{MAF_DISTRIBUTION} version must be exactly {MAF_LOCKED_VERSION}"
        )
    if not _mcp_version_satisfies(mcp_version):
        blockers.append(f"{MCP_DISTRIBUTION} version must satisfy {MCP_VERSION_SPECIFIER}")

    if module is not None:
        if not (getattr(module, "Agent", None) or getattr(module, "ChatAgent", None)):
            blockers.append("agent_framework.Agent/ChatAgent is unavailable")
        else:
            notices.append("Agent/ChatAgent symbol is present")

    status = "blocked" if blockers else "ready"
    return MAFCompatibilityReport(
        status=status,
        package_version=package_version,
        mcp_version=mcp_version,
        blockers=tuple(blockers),
        notices=tuple(notices),
    )

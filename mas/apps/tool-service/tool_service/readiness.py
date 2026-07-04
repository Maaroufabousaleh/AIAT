"""Runtime readiness metadata for adapter-backed tools.

Registration describes the product contract; readiness describes whether the
current deployment can execute that contract. Keeping both prevents agents
from being offered tools whose external adapter is absent.
"""

from __future__ import annotations

import os
import shutil
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .config import Settings


_ENV_ADAPTERS = {
    "code.review": "TOOL_CODE_REVIEW_COMMAND",
    "infra.provision": "TOOL_INFRA_PROVISION_COMMAND",
    "monitoring.setup": "TOOL_MONITORING_SETUP_COMMAND",
}
_SANDBOX_TOOLS = {"command.run_safe", "security.scan", "test.run"}


def tool_readiness(tool_name: str, settings: Settings) -> dict[str, Any]:
    """Return non-secret readiness metadata for one canonical tool."""
    if tool_name in _SANDBOX_TOOLS:
        configured = bool(os.getenv("TOOL_SANDBOX_COMMAND", "").strip())
        return {
            "available": configured,
            "configured": configured,
            "unavailable_reason": None if configured else "TOOL_SANDBOX_COMMAND_not_configured",
        }

    env_name = _ENV_ADAPTERS.get(tool_name)
    if env_name:
        configured = bool(os.getenv(env_name, "").strip())
        return {
            "available": configured,
            "configured": configured,
            "unavailable_reason": None if configured else f"{env_name}_not_configured",
        }

    if tool_name == "mcp.invoke":
        configured = bool(settings.mcp_servers or settings.mcp_transport_endpoints.get(tool_name))
        return {
            "available": configured,
            "configured": configured,
            "unavailable_reason": None if configured else "MCP_transport_endpoint_not_configured",
        }

    if tool_name == "iac.plan":
        available = bool(shutil.which("opentofu") or shutil.which("tofu"))
        return {
            "available": available,
            "configured": available,
            "unavailable_reason": None if available else "opentofu_binary_not_found",
        }

    if tool_name == "diagram.render":
        available = shutil.which("mmdc") is not None
        return {
            "available": available,
            "configured": available,
            "unavailable_reason": None if available else "mmdc_binary_not_found",
        }

    if tool_name == "document.ingest":
        docling_available = shutil.which("docling") is not None
        return {
            "available": True,
            "configured": True,
            "degraded": not docling_available,
            "backend": "docling" if docling_available else "plain_text_fallback",
            "unavailable_reason": None,
        }

    return {
        "available": True,
        "configured": True,
        "unavailable_reason": None,
    }

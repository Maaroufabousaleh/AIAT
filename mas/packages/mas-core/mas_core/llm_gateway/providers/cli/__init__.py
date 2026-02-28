"""CLI-based LLM providers (local subprocess).

Importing this package registers all built-in CLI providers into the global
``MODEL_REGISTRY``.
"""

from . import copilot as copilot  # noqa: F401
from .copilot import COPILOT_BASE_ARGS, COPILOT_COST_MAP, COPILOT_PROVIDER, CopilotModelScanner

__all__ = [
    "COPILOT_BASE_ARGS",
    "COPILOT_COST_MAP",
    "COPILOT_PROVIDER",
    "CopilotModelScanner",
]

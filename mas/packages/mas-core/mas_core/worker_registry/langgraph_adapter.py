"""Compatibility import for the canonical LangGraph adapter.

The implementation lives in :mod:`runtime_adapters`. This module remains
stable for older factory/configuration and certification imports while the
repository migrates to one specialist adapter family.
"""

from .runtime_adapters import LangGraphAdapter, LangGraphCapabilities

__all__ = ["LangGraphAdapter", "LangGraphCapabilities"]

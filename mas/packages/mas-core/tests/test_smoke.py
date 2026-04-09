"""
Smoke tests for mas_core package structure.
Full implementation deferred to Phase 7+.
"""
import importlib


def test_mas_core_importable():
    """Package must import without errors."""
    import mas_core  # noqa: F401


def test_all_submodules_importable():
    """Each submodule must be importable (stubs are in place)."""
    submodules = [
        "mas_core.protocols",
        "mas_core.policy",
        "mas_core.llm_gateway",
        "mas_core.agent_runtime",
        "mas_core.workflow",
        "mas_core.memory",
        "mas_core.util",
    ]
    for name in submodules:
        mod = importlib.import_module(name)
        assert mod is not None, f"Failed to import {name}"


def test_protocols_module_has_docstring():
    """Protocols module must document its planned exports."""
    from mas_core import protocols
    assert protocols.__doc__ is not None
    assert "MessageEnvelope" in protocols.__doc__

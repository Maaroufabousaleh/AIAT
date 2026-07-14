"""Deterministic Semgrep fixture; never import or execute this file."""


def unsafe_fixture(user_input: str):
    """Intentionally match the aiat-python-dangerous-eval test rule."""
    return eval(user_input)

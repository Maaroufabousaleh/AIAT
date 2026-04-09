"""Smoke tests for mas_tools_sdk package."""


def test_mas_tools_sdk_importable():
    import mas_tools_sdk  # noqa: F401


def test_sdk_documents_base_tool():
    from mas_tools_sdk import __doc__ as doc
    assert doc is not None
    assert "BaseTool" in doc

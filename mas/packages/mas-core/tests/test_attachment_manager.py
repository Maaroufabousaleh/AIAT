"""Tests for TempAttachmentManager — ephemeral file staging for LLM content."""

from __future__ import annotations

import base64
import json
import struct
import zlib
from pathlib import Path
from typing import Any

import pytest

from mas_core.agent_runtime.attachment_manager import (
    SavedFile,
    TempAttachmentManager,
    _DATA_URL_RE,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_png(width: int = 2, height: int = 2, r: int = 255, g: int = 0, b: int = 0) -> bytes:
    """Generate a tiny valid PNG (RGB, no palette)."""

    def _chunk(chunk_type: bytes, data: bytes) -> bytes:
        c = chunk_type + data
        return struct.pack(">I", len(data)) + c + struct.pack(">I", zlib.crc32(c) & 0xFFFFFFFF)

    ihdr_data = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    ihdr = _chunk(b"IHDR", ihdr_data)
    raw_rows = b""
    for _ in range(height):
        raw_rows += b"\x00" + bytes([r, g, b]) * width
    idat = _chunk(b"IDAT", zlib.compress(raw_rows))
    iend = _chunk(b"IEND", b"")
    return b"\x89PNG\r\n\x1a\n" + ihdr + idat + iend


def _make_data_url(data: bytes, mime: str = "image/png") -> str:
    return f"data:{mime};base64," + base64.b64encode(data).decode()


# ---------------------------------------------------------------------------
# Unit tests — SavedFile
# ---------------------------------------------------------------------------


class TestSavedFile:
    def test_is_image_png(self, tmp_path: Path) -> None:
        f = SavedFile(path=tmp_path / "test.png", mime_type="image/png", sha256="abc", size_bytes=10)
        assert f.is_image is True

    def test_is_image_json(self, tmp_path: Path) -> None:
        f = SavedFile(path=tmp_path / "test.json", mime_type="application/json", sha256="abc", size_bytes=10)
        assert f.is_image is False

    def test_data_url_round_trip(self, tmp_path: Path) -> None:
        raw = _make_png()
        p = tmp_path / "img.png"
        p.write_bytes(raw)
        f = SavedFile(path=p, mime_type="image/png", sha256="x", size_bytes=len(raw))
        url = f.data_url
        assert url.startswith("data:image/png;base64,")
        decoded = base64.b64decode(url.split(",", 1)[1])
        assert decoded == raw

    def test_file_uri(self, tmp_path: Path) -> None:
        p = tmp_path / "test.txt"
        f = SavedFile(path=p, mime_type="text/plain", sha256="x", size_bytes=0)
        assert f.file_uri.startswith("file://")


# ---------------------------------------------------------------------------
# Unit tests — TempAttachmentManager
# ---------------------------------------------------------------------------


class TestTempAttachmentManagerLifecycle:
    def test_setup_creates_dir(self) -> None:
        mgr = TempAttachmentManager()
        try:
            p = mgr.setup()
            assert p.exists()
            assert p.is_dir()
        finally:
            mgr.cleanup()

    def test_cleanup_removes_dir(self) -> None:
        mgr = TempAttachmentManager()
        p = mgr.setup()
        mgr.cleanup()
        assert not p.exists()
        assert mgr.temp_dir is None
        assert mgr.saved_files == []

    def test_double_cleanup_is_safe(self) -> None:
        mgr = TempAttachmentManager()
        mgr.setup()
        mgr.cleanup()
        mgr.cleanup()  # Should not raise

    @pytest.mark.asyncio
    async def test_async_context_manager(self) -> None:
        async with TempAttachmentManager() as mgr:
            p = mgr.temp_dir
            assert p is not None and p.exists()
        assert not p.exists()  # type: ignore[union-attr]


class TestSaveBytes:
    def test_save_png(self) -> None:
        mgr = TempAttachmentManager()
        mgr.setup()
        try:
            raw = _make_png()
            saved = mgr.save_bytes(raw, mime_type="image/png")
            assert saved is not None
            assert saved.path.exists()
            assert saved.path.read_bytes() == raw
            assert saved.mime_type == "image/png"
            assert saved.size_bytes == len(raw)
            assert saved.is_image is True
            assert saved in mgr.saved_files
        finally:
            mgr.cleanup()

    def test_respects_max_file_bytes(self) -> None:
        mgr = TempAttachmentManager(max_file_bytes=10)
        mgr.setup()
        try:
            saved = mgr.save_bytes(b"x" * 11, mime_type="text/plain")
            assert saved is None
        finally:
            mgr.cleanup()

    def test_filename_hint(self) -> None:
        mgr = TempAttachmentManager()
        mgr.setup()
        try:
            saved = mgr.save_bytes(b"hello", filename_hint="report.txt")
            assert saved is not None
            assert "report.txt" in saved.path.name
        finally:
            mgr.cleanup()


class TestExtractDataUrl:
    def test_valid_png_data_url(self) -> None:
        raw = _make_png()
        url = _make_data_url(raw, "image/png")
        result = TempAttachmentManager.extract_data_url(url)
        assert result is not None
        mime, decoded = result
        assert mime == "image/png"
        assert decoded == raw

    def test_invalid_string(self) -> None:
        assert TempAttachmentManager.extract_data_url("just some text") is None

    def test_invalid_base64(self) -> None:
        assert TempAttachmentManager.extract_data_url("data:image/png;base64,!!!") is None


class TestProcessToolResult:
    def test_plain_string_passthrough(self) -> None:
        mgr = TempAttachmentManager()
        mgr.setup()
        try:
            result = mgr.process_tool_result("hello world")
            assert result == "hello world"
        finally:
            mgr.cleanup()

    def test_data_url_string_yields_content_array(self) -> None:
        mgr = TempAttachmentManager()
        mgr.setup()
        try:
            raw = _make_png()
            url = _make_data_url(raw)
            result = mgr.process_tool_result(url)
            assert isinstance(result, list)
            assert len(result) == 2  # text + image_url
            assert result[0]["type"] == "text"
            assert result[1]["type"] == "image_url"
            assert result[1]["image_url"]["url"].startswith("data:image/png;base64,")
        finally:
            mgr.cleanup()

    def test_dict_with_data_url_field(self) -> None:
        mgr = TempAttachmentManager()
        mgr.setup()
        try:
            raw = _make_png()
            url = _make_data_url(raw)
            result = mgr.process_tool_result({"image": url, "caption": "test chart"})
            assert isinstance(result, list)
            # Should have text + image parts
            types = [p["type"] for p in result]
            assert "text" in types
            assert "image_url" in types
            # text part should mention "caption" from the dict
            text_part = next(p for p in result if p["type"] == "text")
            assert "caption" in text_part["text"]
        finally:
            mgr.cleanup()

    def test_dict_without_data_url(self) -> None:
        mgr = TempAttachmentManager()
        mgr.setup()
        try:
            result = mgr.process_tool_result({"status": "ok", "count": 42})
            assert isinstance(result, str)
            parsed = json.loads(result)
            assert parsed["status"] == "ok"
            assert parsed["count"] == 42
        finally:
            mgr.cleanup()

    def test_bytes_result(self) -> None:
        mgr = TempAttachmentManager()
        mgr.setup()
        try:
            raw = _make_png()
            result = mgr.process_tool_result(raw)
            assert isinstance(result, list)
        finally:
            mgr.cleanup()

    def test_list_inside_dict(self) -> None:
        mgr = TempAttachmentManager()
        mgr.setup()
        try:
            raw = _make_png()
            url = _make_data_url(raw)
            result = mgr.process_tool_result({"images": [url], "text": "charts"})
            assert isinstance(result, list)
        finally:
            mgr.cleanup()


class TestStripBase64ForCheckpoint:
    def test_strips_image_url_data_urls(self) -> None:
        mgr = TempAttachmentManager()
        raw = _make_png()
        messages: list[dict[str, Any]] = [
            {
                "role": "tool",
                "content": [
                    {"type": "text", "text": "Result:"},
                    {
                        "type": "image_url",
                        "image_url": {"url": _make_data_url(raw)},
                    },
                ],
            },
            {"role": "assistant", "content": "Got it."},
        ]
        cleaned = mgr.strip_base64_for_checkpoint(messages)
        assert len(cleaned) == 2
        # First message should have data-URL replaced
        parts = cleaned[0]["content"]
        assert isinstance(parts, list)
        assert all(p.get("type") != "image_url" for p in parts)
        assert "stripped" in parts[1]["text"]
        # Second message unchanged
        assert cleaned[1]["content"] == "Got it."

    def test_preserves_non_data_url_images(self) -> None:
        mgr = TempAttachmentManager()
        messages: list[dict[str, Any]] = [
            {
                "role": "tool",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {"url": "https://example.com/img.png"},
                    },
                ],
            },
        ]
        cleaned = mgr.strip_base64_for_checkpoint(messages)
        assert cleaned[0]["content"][0]["type"] == "image_url"


class TestDataUrlRegex:
    def test_matches_valid(self) -> None:
        assert _DATA_URL_RE.match("data:image/png;base64,iVBORw0KGgo=")
        assert _DATA_URL_RE.match("data:application/pdf;base64,JVBERi==")

    def test_rejects_invalid(self) -> None:
        assert _DATA_URL_RE.match("https://example.com") is None
        assert _DATA_URL_RE.match("not a data url") is None


# ---------------------------------------------------------------------------
# CLI-specific tests
# ---------------------------------------------------------------------------


class TestGetCliArgs:
    def test_returns_empty_when_no_files(self) -> None:
        mgr = TempAttachmentManager()
        mgr.setup()
        try:
            assert mgr.get_cli_args() == []
        finally:
            mgr.cleanup()

    def test_returns_add_dir_flag_after_save(self) -> None:
        mgr = TempAttachmentManager()
        mgr.setup()
        try:
            mgr.save_bytes(_make_png(), mime_type="image/png")
            args = mgr.get_cli_args()
            assert args[0] == "--add-dir"
            assert args[1] == str(mgr.temp_dir)
        finally:
            mgr.cleanup()

    def test_returns_empty_before_setup(self) -> None:
        mgr = TempAttachmentManager()
        assert mgr.get_cli_args() == []


class TestBuildCliFileReferences:
    def test_returns_empty_when_no_files(self) -> None:
        mgr = TempAttachmentManager()
        mgr.setup()
        try:
            assert mgr.build_cli_file_references() == ""
        finally:
            mgr.cleanup()

    def test_lists_saved_files(self) -> None:
        mgr = TempAttachmentManager()
        mgr.setup()
        try:
            raw = _make_png()
            mgr.save_bytes(raw, mime_type="image/png")
            refs = mgr.build_cli_file_references()
            assert "file(s) are available" in refs
            assert "image/png" in refs
            assert str(mgr.temp_dir) in refs
            assert "examine these files" in refs
        finally:
            mgr.cleanup()

    def test_multiple_files(self) -> None:
        mgr = TempAttachmentManager()
        mgr.setup()
        try:
            mgr.save_bytes(_make_png(), mime_type="image/png")
            mgr.save_bytes(b"hello world", mime_type="text/plain")
            refs = mgr.build_cli_file_references()
            assert "image/png" in refs
            assert "text/plain" in refs
        finally:
            mgr.cleanup()


class TestProcessImageUrlsForCli:
    def test_saves_data_url_images(self) -> None:
        mgr = TempAttachmentManager()
        mgr.setup()
        try:
            raw = _make_png()
            url = _make_data_url(raw)
            saved = mgr.process_image_urls_for_cli([url])
            assert len(saved) == 1
            assert saved[0].is_image
            assert saved[0].path.exists()
            assert saved[0].path.read_bytes() == raw
        finally:
            mgr.cleanup()

    def test_skips_remote_urls(self) -> None:
        mgr = TempAttachmentManager()
        mgr.setup()
        try:
            saved = mgr.process_image_urls_for_cli(["https://example.com/img.png"])
            assert len(saved) == 0
        finally:
            mgr.cleanup()

    def test_handles_mixed_urls(self) -> None:
        mgr = TempAttachmentManager()
        mgr.setup()
        try:
            raw = _make_png()
            saved = mgr.process_image_urls_for_cli([
                _make_data_url(raw),
                "https://example.com/other.png",
                _make_data_url(raw),
            ])
            assert len(saved) == 2  # only data URLs saved
        finally:
            mgr.cleanup()

    def test_empty_list(self) -> None:
        mgr = TempAttachmentManager()
        mgr.setup()
        try:
            saved = mgr.process_image_urls_for_cli([])
            assert saved == []
        finally:
            mgr.cleanup()


class TestCliIntegrationFlow:
    """End-to-end test of the CLI attachment flow (no actual CLI call)."""

    def test_full_cli_attachment_workflow(self) -> None:
        """Simulate the full flow: extract images → save → get CLI args → build refs."""
        mgr = TempAttachmentManager()
        mgr.setup()
        try:
            raw = _make_png(4, 4, 0, 128, 255)
            url = _make_data_url(raw)

            # Simulate what _call_cli does: extract images from content
            from mas_core.llm_gateway.client import LLMGatewayClient

            text, image_urls = LLMGatewayClient._extract_text_and_images([
                {"type": "text", "text": "Here is a chart"},
                {"type": "image_url", "image_url": {"url": url}},
            ])
            assert text == "Here is a chart"
            assert len(image_urls) == 1

            # Save image URLs for CLI
            saved = mgr.process_image_urls_for_cli(image_urls)
            assert len(saved) == 1

            # Build CLI args
            cli_args = mgr.get_cli_args()
            assert cli_args == ["--add-dir", str(mgr.temp_dir)]

            # Build file references for prompt
            refs = mgr.build_cli_file_references()
            assert str(saved[0].path) in refs
            assert "image/png" in refs

            # Verify the file is actually on disk
            assert saved[0].path.exists()
            assert saved[0].path.read_bytes() == raw
        finally:
            mgr.cleanup()
            # Files should be gone after cleanup
            assert not any(f.path.exists() for f in saved)

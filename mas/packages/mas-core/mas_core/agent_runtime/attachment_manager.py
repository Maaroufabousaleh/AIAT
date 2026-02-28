"""TempAttachmentManager — ephemeral file staging for LLM multimodal content.

Primary use-case: **CLI-based LLM providers** (GitHub Copilot CLI, etc.)
that cannot accept inline base64 images but *can* read files from disk
when the path is referenced in the prompt and the directory is allowed.

When an agent tool returns binary data (images, PDFs, etc.) as base64
data-URLs or raw bytes, the attachment manager:

1. Creates a per-call temp sub-directory under a configurable root.
2. Writes files to disk with content-addressed names (SHA-256 prefix).
3. Returns either:
   - **CLI mode**: file paths + ``--add-dir`` flags for the CLI command,
     with file references injected into the prompt text.
   - **API mode**: OpenAI-style multipart ``content`` arrays with
     ``image_url`` parts (base64 data-URLs).
4. Cleans up the sub-directory automatically after the LLM response
   (via async context-manager or explicit ``cleanup()``).

The manager is designed to be **lightweight** and **concurrency-safe** — each
``think()`` iteration gets its own short-lived instance so there are no
shared-state concerns.

Supported content detection
---------------------------
* ``data:<mime>;base64,<b64data>`` data-URLs  (images, PDFs, etc.)
* Raw ``bytes`` / ``bytearray`` values in tool result dicts
* ``BlobRef`` dicts (``{"bucket": …, "key": …}``) — downloaded on demand
  via an optional ``BlobClient``

Size safeguards
---------------
* Files larger than ``max_file_bytes`` (default 20 MB) are rejected.
* The temp directory is always cleaned up, even on exceptions.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import logging
import os
import re
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Regex for data-URL prefix: data:<mime>;base64,
_DATA_URL_RE = re.compile(
    r"^data:(?P<mime>[a-zA-Z0-9_.+-]+/[a-zA-Z0-9_.+-]+);base64,(?P<data>.+)$",
    re.DOTALL,
)

# Mime → file extension mapping (most common types for agents)
_MIME_EXT: dict[str, str] = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/gif": ".gif",
    "image/webp": ".webp",
    "image/svg+xml": ".svg",
    "image/bmp": ".bmp",
    "application/pdf": ".pdf",
    "text/plain": ".txt",
    "text/csv": ".csv",
    "text/html": ".html",
    "application/json": ".json",
    "application/xml": ".xml",
}

# Image MIME types that should be sent as ``image_url`` content parts
_IMAGE_MIMES: frozenset[str] = frozenset({
    "image/png",
    "image/jpeg",
    "image/gif",
    "image/webp",
    "image/bmp",
})

DEFAULT_MAX_FILE_BYTES: int = 20 * 1024 * 1024  # 20 MB
DEFAULT_ROOT_DIR: str | None = None  # None → system temp dir


# ---------------------------------------------------------------------------
# SavedFile — metadata about a file written to the temp dir
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SavedFile:
    """Metadata about a single file saved to the temp staging directory."""

    path: Path
    mime_type: str
    sha256: str
    size_bytes: int

    @property
    def is_image(self) -> bool:
        return self.mime_type in _IMAGE_MIMES

    @property
    def data_url(self) -> str:
        """Re-encode the file as a base64 data-URL (for LLM content arrays)."""
        raw = self.path.read_bytes()
        b64 = base64.b64encode(raw).decode("ascii")
        return f"data:{self.mime_type};base64,{b64}"

    @property
    def file_uri(self) -> str:
        """Return a ``file://`` URI pointing to the saved file."""
        return self.path.as_uri()


# ---------------------------------------------------------------------------
# TempAttachmentManager
# ---------------------------------------------------------------------------


class TempAttachmentManager:
    """Create a temp sub-dir, stage files from tool results, and clean up.

    Usage (async context-manager)::

        async with TempAttachmentManager() as mgr:
            content = mgr.process_tool_result(tool_result)
            # content is now an OpenAI content array or plain string
            ...
        # temp dir deleted here

    Or manually::

        mgr = TempAttachmentManager()
        mgr.setup()
        try:
            content = mgr.process_tool_result(tool_result)
        finally:
            mgr.cleanup()
    """

    def __init__(
        self,
        *,
        root_dir: str | None = DEFAULT_ROOT_DIR,
        max_file_bytes: int = DEFAULT_MAX_FILE_BYTES,
        prefix: str = "mas_attach_",
    ) -> None:
        self._root_dir = root_dir
        self._max_file_bytes = max_file_bytes
        self._prefix = prefix
        self._temp_dir: Path | None = None
        self._saved_files: list[SavedFile] = []

    # -- Context-manager interface ------------------------------------------

    async def __aenter__(self) -> TempAttachmentManager:
        self.setup()
        return self

    async def __aexit__(self, *_: object) -> None:
        self.cleanup()

    # -- Setup / teardown ---------------------------------------------------

    def setup(self) -> Path:
        """Create the temp sub-directory. Returns the Path."""
        if self._temp_dir is not None:
            return self._temp_dir
        self._temp_dir = Path(
            tempfile.mkdtemp(prefix=self._prefix, dir=self._root_dir)
        )
        logger.debug("Attachment staging dir created: %s", self._temp_dir)
        return self._temp_dir

    def cleanup(self) -> None:
        """Remove the temp sub-directory and all contents."""
        if self._temp_dir is None:
            return
        try:
            shutil.rmtree(self._temp_dir, ignore_errors=True)
            logger.debug(
                "Attachment staging dir removed: %s (%d files)",
                self._temp_dir,
                len(self._saved_files),
            )
        except Exception:
            logger.warning(
                "Failed to clean up staging dir: %s", self._temp_dir, exc_info=True,
            )
        finally:
            self._temp_dir = None
            self._saved_files.clear()

    @property
    def temp_dir(self) -> Path | None:
        return self._temp_dir

    @property
    def saved_files(self) -> list[SavedFile]:
        return list(self._saved_files)

    # -- Core: save a single binary blob ------------------------------------

    def save_bytes(
        self,
        data: bytes,
        *,
        mime_type: str = "application/octet-stream",
        filename_hint: str | None = None,
    ) -> SavedFile | None:
        """Write *data* to the temp dir. Returns ``SavedFile`` or ``None`` on skip."""
        if len(data) > self._max_file_bytes:
            logger.warning(
                "Attachment too large (%d bytes > %d max), skipping",
                len(data),
                self._max_file_bytes,
            )
            return None

        if self._temp_dir is None:
            self.setup()
        assert self._temp_dir is not None  # noqa: S101

        digest = hashlib.sha256(data).hexdigest()[:16]
        ext = _MIME_EXT.get(mime_type, "")
        if filename_hint:
            stem = re.sub(r"[^\w.-]", "_", filename_hint)
            name = f"{digest}_{stem}"
        else:
            name = f"{digest}{ext}"

        file_path = self._temp_dir / name
        file_path.write_bytes(data)

        saved = SavedFile(
            path=file_path,
            mime_type=mime_type,
            sha256=hashlib.sha256(data).hexdigest(),
            size_bytes=len(data),
        )
        self._saved_files.append(saved)
        logger.debug(
            "Attachment saved: %s (%s, %d bytes)",
            file_path.name,
            mime_type,
            len(data),
        )
        return saved

    # -- Data-URL detection and extraction ----------------------------------

    @staticmethod
    def extract_data_url(value: str) -> tuple[str, bytes] | None:
        """Parse a ``data:<mime>;base64,<data>`` string.

        Returns ``(mime_type, raw_bytes)`` or ``None`` if not a data URL.
        """
        m = _DATA_URL_RE.match(value)
        if m is None:
            return None
        mime = m.group("mime")
        try:
            raw = base64.b64decode(m.group("data"), validate=True)
        except Exception:
            return None
        return mime, raw

    # -- High-level: process a tool result ----------------------------------

    def process_tool_result(
        self,
        result: Any,
        *,
        tool_name: str = "",
    ) -> str | list[dict[str, Any]]:
        """Inspect a tool result and extract embedded files.

        Returns
        -------
        str
            If no files were found, the original JSON-serialised result.
        list[dict]
            An OpenAI-style multipart content array with ``text`` and
            ``image_url`` parts when files are detected.
        """
        files: list[SavedFile] = []
        text_result: str = ""

        if isinstance(result, str):
            extracted = self._extract_from_string(result)
            if extracted:
                files.extend(extracted)
                text_result = "[File attachment(s) extracted — see image(s) below]"
            else:
                return result  # plain text, nothing to do
        elif isinstance(result, dict):
            text_result, extracted = self._extract_from_dict(result, tool_name)
            files.extend(extracted)
        elif isinstance(result, (bytes, bytearray)):
            saved = self.save_bytes(bytes(result))
            if saved:
                files.append(saved)
                text_result = f"[Binary result saved: {saved.path.name}]"
            else:
                text_result = "[Binary result too large, skipped]"
        else:
            import json as _json

            return _json.dumps(result, default=str)

        if not files:
            import json as _json

            return _json.dumps(result, default=str) if not text_result else text_result

        # Build OpenAI multipart content array
        return self._build_content_array(text_result, files)

    # -- Internal extraction helpers ----------------------------------------

    def _extract_from_string(self, value: str) -> list[SavedFile]:
        """Try to extract a single data-URL from a plain string."""
        parsed = self.extract_data_url(value.strip())
        if parsed is None:
            return []
        mime, raw = parsed
        saved = self.save_bytes(raw, mime_type=mime)
        return [saved] if saved else []

    def _extract_from_dict(
        self,
        result: dict[str, Any],
        tool_name: str,
    ) -> tuple[str, list[SavedFile]]:
        """Walk a dict result looking for data-URLs and raw bytes.

        Returns ``(text_summary, files_found)``.
        """
        import json as _json

        files: list[SavedFile] = []
        sanitised: dict[str, Any] = {}

        for key, val in result.items():
            if isinstance(val, str):
                parsed = self.extract_data_url(val.strip())
                if parsed:
                    mime, raw = parsed
                    saved = self.save_bytes(
                        raw, mime_type=mime, filename_hint=f"{tool_name}_{key}",
                    )
                    if saved:
                        files.append(saved)
                        sanitised[key] = f"[saved:{saved.path.name}]"
                        continue
            if isinstance(val, (bytes, bytearray)):
                saved = self.save_bytes(
                    bytes(val), filename_hint=f"{tool_name}_{key}",
                )
                if saved:
                    files.append(saved)
                    sanitised[key] = f"[saved:{saved.path.name}]"
                    continue
            # Recurse one level into lists for data-URL strings
            if isinstance(val, list):
                new_list: list[Any] = []
                for item in val:
                    if isinstance(item, str):
                        parsed = self.extract_data_url(item.strip())
                        if parsed:
                            mime, raw = parsed
                            saved = self.save_bytes(raw, mime_type=mime)
                            if saved:
                                files.append(saved)
                                new_list.append(f"[saved:{saved.path.name}]")
                                continue
                    new_list.append(item)
                sanitised[key] = new_list
                continue
            sanitised[key] = val

        text = _json.dumps(sanitised, default=str)
        return text, files

    @staticmethod
    def _build_content_array(
        text: str,
        files: list[SavedFile],
    ) -> list[dict[str, Any]]:
        """Build an OpenAI multipart ``content`` array.

        Image files become ``{"type": "image_url", "image_url": {"url": ...}}``
        parts using base64 data-URLs (required by OpenAI — file:// not accepted).

        Non-image files are described in the text part.
        """
        parts: list[dict[str, Any]] = []
        non_image_descriptions: list[str] = []

        for f in files:
            if f.is_image:
                # Use data-URL for the LLM (most providers require this)
                parts.append({
                    "type": "image_url",
                    "image_url": {"url": f.data_url, "detail": "auto"},
                })
            else:
                non_image_descriptions.append(
                    f"[Attached file: {f.path.name} ({f.mime_type}, {f.size_bytes:,} bytes)]"
                )

        # Prepend text part
        full_text = text
        if non_image_descriptions:
            full_text += "\n" + "\n".join(non_image_descriptions)

        parts.insert(0, {"type": "text", "text": full_text})
        return parts

    # -- CLI-specific helpers -----------------------------------------------

    def get_cli_args(self) -> list[str]:
        """Return extra CLI flags to grant the CLI model access to saved files.

        For GitHub Copilot CLI this returns ``["--add-dir", "<temp_dir>"]``
        which allows the model to read files from the staging directory.

        Returns an empty list if no files have been saved yet.
        """
        if self._temp_dir is None or not self._saved_files:
            return []
        return ["--add-dir", str(self._temp_dir)]

    def build_cli_file_references(self) -> str:
        """Build a prompt-text block that tells the CLI model about saved files.

        Returns a human-readable block like::

            The following file(s) are available for you to read from disk:
            - C:\\tmp\\mas_attach_xxx\\a1b2c3d4.png  (image/png, 12,345 bytes)
            - C:\\tmp\\mas_attach_xxx\\e5f6g7h8.pdf  (application/pdf, 98,765 bytes)

            Please examine these files and incorporate their content in your response.

        Returns an empty string when no files have been saved.
        """
        if not self._saved_files:
            return ""
        lines = ["The following file(s) are available for you to read from disk:"]
        for f in self._saved_files:
            lines.append(
                f"  - {f.path}  ({f.mime_type}, {f.size_bytes:,} bytes)"
            )
        lines.append("")
        lines.append(
            "Please examine these files and incorporate their content "
            "in your response."
        )
        return "\n".join(lines)

    def process_image_urls_for_cli(
        self,
        image_urls: list[str],
    ) -> list[SavedFile]:
        """Save data-URL or remote images to disk for CLI model consumption.

        Each ``data:<mime>;base64,<data>`` URL is decoded and written to the
        staging directory.  Returns the list of successfully saved files.

        Plain ``https://`` URLs are not downloaded — they are noted as text
        references only.
        """
        saved: list[SavedFile] = []
        for i, url in enumerate(image_urls):
            parsed = self.extract_data_url(url.strip())
            if parsed is not None:
                mime, raw = parsed
                sf = self.save_bytes(
                    raw,
                    mime_type=mime,
                    filename_hint=f"image_{i}",
                )
                if sf:
                    saved.append(sf)
            else:
                # Remote URL — we can't download it for CLI, just warn
                logger.debug(
                    "CLI attachment: skipping remote URL (not a data-URL): %.80s…",
                    url,
                )
        return saved

    # -- Checkpoint helpers -------------------------------------------------

    def strip_base64_for_checkpoint(
        self,
        messages: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Return a copy of *messages* with base64 data-URLs replaced by placeholders.

        This prevents multi-MB base64 blobs from being persisted in Postgres
        checkpoints.  The original files remain on disk until ``cleanup()``
        is called (which happens after the LLM response).
        """
        cleaned: list[dict[str, Any]] = []
        for msg in messages:
            content = msg.get("content")
            if isinstance(content, list):
                new_parts: list[dict[str, Any]] = []
                for part in content:
                    if (
                        isinstance(part, dict)
                        and part.get("type") == "image_url"
                        and isinstance(part.get("image_url"), dict)
                    ):
                        url = part["image_url"].get("url", "")
                        if url.startswith("data:"):
                            # Replace data-URL with a placeholder
                            new_parts.append({
                                "type": "text",
                                "text": "[image data-URL stripped for checkpoint]",
                            })
                            continue
                    new_parts.append(part)
                cleaned.append({**msg, "content": new_parts})
            elif isinstance(content, str) and _DATA_URL_RE.match(content.strip()):
                cleaned.append({
                    **msg,
                    "content": "[data-URL stripped for checkpoint]",
                })
            else:
                cleaned.append(msg)
        return cleaned

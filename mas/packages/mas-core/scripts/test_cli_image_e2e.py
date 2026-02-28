"""End-to-end test: save a real image, pass it to Copilot CLI via --add-dir.

Usage::

    python -m packages.mas-core.scripts.test_cli_image_e2e

This script:
1. Creates a temp directory and writes a small PNG to it
2. Calls Copilot CLI with --add-dir pointing to the temp dir
3. Asks the model to describe the image at the given path
4. Prints the response
5. Cleans up
"""
from __future__ import annotations

import asyncio
import struct
import sys
import zlib

# Ensure project root is on PYTHONPATH
sys.path.insert(0, r"C:\projects\AIAT\mas\packages\mas-core")

from mas_core.agent_runtime.attachment_manager import TempAttachmentManager


def make_test_png(width: int = 8, height: int = 8) -> bytes:
    """Generate a small red/blue checkerboard PNG."""
    def _chunk(chunk_type: bytes, data: bytes) -> bytes:
        c = chunk_type + data
        return struct.pack(">I", len(data)) + c + struct.pack(">I", zlib.crc32(c) & 0xFFFFFFFF)

    ihdr_data = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    ihdr = _chunk(b"IHDR", ihdr_data)
    raw_rows = b""
    for y in range(height):
        raw_rows += b"\x00"
        for x in range(width):
            if (x + y) % 2 == 0:
                raw_rows += bytes([255, 0, 0])    # red pixel
            else:
                raw_rows += bytes([0, 0, 255])     # blue pixel
    idat = _chunk(b"IDAT", zlib.compress(raw_rows))
    iend = _chunk(b"IEND", b"")
    return b"\x89PNG\r\n\x1a\n" + ihdr + idat + iend


async def main() -> None:
    mgr = TempAttachmentManager()
    mgr.setup()
    try:
        # 1. Save a test image
        raw = make_test_png()
        saved = mgr.save_bytes(raw, mime_type="image/png", filename_hint="checkerboard")
        assert saved is not None
        print(f"[1] Image saved: {saved.path} ({saved.size_bytes} bytes)")

        # 2. Build CLI args
        cli_args = mgr.get_cli_args()
        print(f"[2] CLI args: {cli_args}")

        # 3. Build file references for the prompt
        refs = mgr.build_cli_file_references()
        print(f"[3] File references:\n{refs}")

        # 4. Build a full Copilot CLI command
        import shutil
        binary = shutil.which("copilot")
        if binary is None:
            # Fallback to known path
            binary = r"c:\Users\Maaro\AppData\Roaming\Code\User\globalStorage\github.copilot-chat\copilotCli\copilot.BAT"

        prompt = (
            f"Describe the image file I've given you access to. "
            f"The file is at: {saved.path}\n\n{refs}"
        )
        cmd = [
            binary,
            "-s",
            "--no-ask-user",
            "--no-auto-update",
            *cli_args,
            "--model", "gpt-5-mini",
            "-p", prompt,
        ]
        print(f"[4] Command: {' '.join(cmd[:6])} ... (truncated)")
        print(f"    Full prompt: {prompt[:200]}...")

        # 5. Execute
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        text = stdout.decode("utf-8", errors="replace").strip()
        err = stderr.decode("utf-8", errors="replace").strip()

        print(f"\n[5] Exit code: {proc.returncode}")
        if err:
            print(f"    Stderr: {err[:300]}")
        print(f"    Response:\n{text[:1000]}")

        if text and proc.returncode == 0:
            print("\n✓ SUCCESS — Copilot CLI processed the image via --add-dir")
        else:
            print("\n✗ FAILED — see errors above")

    finally:
        mgr.cleanup()
        print("\n[cleanup] Temp dir removed")


if __name__ == "__main__":
    asyncio.run(main())

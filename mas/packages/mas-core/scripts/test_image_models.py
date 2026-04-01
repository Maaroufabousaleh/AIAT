#!/usr/bin/env python
"""Live image-input test for all models that declare supports_images=True.

Generates a tiny 8×8 coloured PNG in memory, encodes it as a base64
data-URL, and sends it to each image-capable model asking to describe
the dominant colour.  Also tests text-only models to confirm they still
work when given a plain text prompt.

Run from the repo root:
    c:/projects/AIAT/.venv/Scripts/python.exe mas/packages/mas-core/scripts/test_image_models.py

Flags:
    --scan-copilot   Scan and register Copilot CLI models first
    --text-too       Also re-test every model with a plain-text prompt
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import os
import struct
import sys
import zlib

# Ensure the package is importable regardless of CWD
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_PACKAGE_ROOT = os.path.dirname(_SCRIPT_DIR)  # mas-core/
sys.path.insert(0, _PACKAGE_ROOT)

from mas_core.llm_gateway import (
    MODEL_REGISTRY,
    CopilotModelScanner,
    LLMGatewayClient,
)
from mas_core.llm_gateway.models import LLMConfig

# ---------------------------------------------------------------------------
# Tiny PNG generator (no Pillow dependency)
# ---------------------------------------------------------------------------


def make_png(width: int = 8, height: int = 8, r: int = 50, g: int = 120, b: int = 220) -> bytes:
    """Generate a minimal solid-colour PNG (RGB, no palette)."""

    def _chunk(chunk_type: bytes, data: bytes) -> bytes:
        c = chunk_type + data
        return struct.pack(">I", len(data)) + c + struct.pack(">I", zlib.crc32(c) & 0xFFFFFFFF)

    # IHDR
    ihdr_data = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)  # 8-bit RGB
    ihdr = _chunk(b"IHDR", ihdr_data)

    # IDAT — raw pixel rows (filter byte 0 + RGB per pixel)
    raw_rows = b""
    for _ in range(height):
        raw_rows += b"\x00" + bytes([r, g, b]) * width
    idat = _chunk(b"IDAT", zlib.compress(raw_rows))

    iend = _chunk(b"IEND", b"")
    return b"\x89PNG\r\n\x1a\n" + ihdr + idat + iend


def make_data_url(png_bytes: bytes) -> str:
    return "data:image/png;base64," + base64.b64encode(png_bytes).decode()


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------


async def test_model_with_image(
    client: LLMGatewayClient,
    model_id: str,
    data_url: str,
    colour_name: str,
) -> bool:
    """Send an image to a model and check if it responds.

    Returns True if the model responded (regardless of correctness).
    """
    entry = MODEL_REGISTRY.get(model_id)
    cap = entry.capabilities if entry else None
    has_images = cap.supports_images if cap else False

    print(f"\n{'=' * 60}")
    print(f"Model:    {model_id}")
    print(f"Images:   {'YES' if has_images else 'NO'}")

    if not has_images:
        print("  SKIP — model does not support images")
        return True

    prompt_text = (
        f"This is a solid {colour_name} image. What colour do you see? Reply in one short sentence."
    )
    messages = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": prompt_text},
                {"type": "image_url", "image_url": {"url": data_url}},
            ],
        }
    ]
    print(f"Prompt:   {prompt_text!r}")
    print(f"Image:    {len(data_url)} chars base64 data-URL")
    print("-" * 60)

    try:
        resp = await client.chat_completion(messages=messages, model=model_id)
        print(f"Response: {resp.text}")
        if resp.usage:
            print(f"Tokens:   in={resp.usage.prompt_tokens}  out={resp.usage.completion_tokens}")
        print("Status:   OK ✓")
        return True
    except Exception as exc:
        print(f"ERROR:    {type(exc).__name__}: {exc}")
        return False


async def test_model_text_only(
    client: LLMGatewayClient,
    model_id: str,
) -> bool:
    """Send a plain text prompt to verify the model still works."""
    print(f"\n{'=' * 60}")
    print(f"Model:    {model_id}  (text-only check)")
    print("-" * 60)

    try:
        resp = await client.chat_completion(
            messages=[{"role": "user", "content": "Reply with one word: hello"}],
            model=model_id,
        )
        print(f"Response: {resp.text}")
        print("Status:   OK ✓")
        return True
    except Exception as exc:
        print(f"ERROR:    {type(exc).__name__}: {exc}")
        return False


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


async def main() -> None:
    parser = argparse.ArgumentParser(description="Live image-input model tester")
    parser.add_argument("--scan-copilot", action="store_true", help="Scan Copilot CLI models first")
    parser.add_argument(
        "--text-too", action="store_true", help="Also test every model with text-only prompt"
    )
    args = parser.parse_args()

    if args.scan_copilot:
        print("Scanning for Copilot CLI models...")
        scanner = CopilotModelScanner()
        entries = await scanner.scan_and_register()
        print(f"  Registered {len(entries)} copilot model(s)")

    # Generate a solid blue test image
    png = make_png(width=8, height=8, r=50, g=120, b=220)
    data_url = make_data_url(png)
    print(f"\nTest image: 8×8 solid blue PNG ({len(data_url)} chars data-URL)")

    config = LLMConfig()
    client = LLMGatewayClient(config)

    results: dict[str, str] = {}

    async with client:
        # Test image-capable models with the image
        all_ids = list(MODEL_REGISTRY.model_ids())
        print(f"\n{'#' * 60}")
        print(f"IMAGE TESTS — {len(all_ids)} registered models")
        print(f"{'#' * 60}")

        for model_id in all_ids:
            entry = MODEL_REGISTRY.get(model_id)
            cap = entry.capabilities if entry else None
            if cap and cap.supports_images:
                ok = await test_model_with_image(client, model_id, data_url, "blue")
                results[f"{model_id} (image)"] = "OK" if ok else "FAIL"
            else:
                results[f"{model_id} (image)"] = "SKIP (no image support)"

        # Optionally test all models with text
        if args.text_too:
            print(f"\n{'#' * 60}")
            print(f"TEXT-ONLY TESTS — {len(all_ids)} registered models")
            print(f"{'#' * 60}")
            for model_id in all_ids:
                ok = await test_model_text_only(client, model_id)
                results[f"{model_id} (text)"] = "OK" if ok else "FAIL"

    # Summary
    print(f"\n{'#' * 60}")
    print("SUMMARY")
    print(f"{'#' * 60}")
    for label, status in results.items():
        icon = "✓" if status == "OK" else ("—" if "SKIP" in status else "✗")
        print(f"  {icon} {label:<40s}  {status}")


if __name__ == "__main__":
    asyncio.run(main())

#!/usr/bin/env python
"""Live smoke-test for all registered LLM providers.

Run from the repo root:
    c:/projects/AIAT/.venv/Scripts/python.exe mas/packages/mas-core/scripts/test_providers_live.py

Optional args:
    --model MODEL_ID      Test only this model (e.g. big-pickle, gpt-5-nano)
    --prompt "text"       Custom prompt (default: "Reply with one word: hello")
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys

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


async def test_model(client: LLMGatewayClient, model_id: str, prompt: str) -> None:
    """Send a single prompt to a model and print the result."""
    print(f"\n{'=' * 60}")
    entry = MODEL_REGISTRY.get(model_id)
    if entry:
        print(f"Model:    {model_id}")
        print(f"Provider: {entry.provider}")
        print(f"Style:    {entry.api_style.value}")
        print(f"Endpoint: {entry.endpoint}")
        cap = entry.capabilities
        rsn_flag = getattr(cap, "supports_reasoning", False)
        ctx = entry.max_context_tokens
        print(f"Context:  {ctx:,} tokens" if ctx else "Context:  unknown")
        print(f"Reason:   {'YES' if rsn_flag else 'NO'}")
        print(f"Images:   {'YES' if cap.supports_images else 'NO':>3s}  {cap.image_how or ''}")
        print(f"PDF:      {'YES' if cap.supports_pdf else 'NO':>3s}  {cap.pdf_how or ''}")
        print(f"Video:    {'YES' if cap.supports_video else 'NO':>3s}  {cap.video_how or ''}")
        if entry.best_for:
            print(f"Best for: {', '.join(entry.best_for)}")
        if entry.limits:
            print(f"Limits:   {', '.join(entry.limits)}")
        if entry.compliance:
            print(f"Comply:   {', '.join(entry.compliance)}")
    else:
        print(f"Model:    {model_id}  (NOT in registry — will use default)")
    print(f"Prompt:   {prompt!r}")
    print("-" * 60)

    try:
        resp = await client.chat_completion(
            messages=[{"role": "user", "content": prompt}],
            model=model_id,
        )
        print(f"Response: {resp.text}")
        if resp.usage:
            print(
                f"Tokens:   in={resp.usage.prompt_tokens}  out={resp.usage.completion_tokens}  total={resp.usage.total_tokens}"
            )
        print("Status:   OK ✓")
    except Exception as exc:
        print(f"ERROR:    {type(exc).__name__}: {exc}")


async def main() -> None:
    parser = argparse.ArgumentParser(description="Live LLM provider tester")
    parser.add_argument("--model", help="Test only this model ID")
    parser.add_argument("--prompt", default="Reply with one word: hello", help="Prompt to send")
    parser.add_argument("--scan-copilot", action="store_true", help="Run Copilot CLI scanner first")
    parser.add_argument("--list", action="store_true", help="List registered models and exit")
    args = parser.parse_args()

    # Optionally scan for Copilot CLI models
    if args.scan_copilot:
        print("Scanning for Copilot CLI models...")
        scanner = CopilotModelScanner()
        entries = await scanner.scan_and_register()
        print(f"  Registered {len(entries)} copilot model(s)")

    # List mode
    if args.list:
        print(f"\nRegistered models ({len(MODEL_REGISTRY)}):\n")
        for mid in MODEL_REGISTRY.model_ids():
            entry = MODEL_REGISTRY.get(mid)
            cap = entry.capabilities
            img = "✓" if cap.supports_images else "✗"
            pdf = "✓" if cap.supports_pdf else "✗"
            vid = "✓" if cap.supports_video else "✗"
            rsn = "✓" if getattr(cap, "supports_reasoning", False) else "✗"
            ctx = entry.max_context_tokens
            ctx_s = f"{ctx:>7,}" if ctx else "      ?"
            print(
                f"  {mid:<30s}  {entry.provider:<10s}  {entry.api_style.value:<20s}  ctx={ctx_s}  rsn={rsn}  img={img}  pdf={pdf}  vid={vid}"
            )
            if entry.best_for:
                print(f"    Best for: {', '.join(entry.best_for)}")
            if entry.limits:
                print(f"    Limits:   {', '.join(entry.limits)}")
            if entry.compliance:
                print(f"    Comply:   {', '.join(entry.compliance)}")
            print()
        print(f"Registered providers ({len(MODEL_REGISTRY.list_providers())}):\n")
        for p in MODEL_REGISTRY.list_providers():
            print(f"  {p.provider_id:<10s}  {p.description}")
        return

    config = LLMConfig()
    client = LLMGatewayClient(config)

    async with client:
        if args.model:
            # Test single model
            await test_model(client, args.model, args.prompt)
        else:
            # Test all registered models
            print(f"Testing all {len(MODEL_REGISTRY)} registered models...\n")
            for model_id in MODEL_REGISTRY.model_ids():
                await test_model(client, model_id, args.prompt)

    print(f"\n{'=' * 60}")
    print("Done.")


if __name__ == "__main__":
    asyncio.run(main())

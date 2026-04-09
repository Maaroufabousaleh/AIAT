#!/usr/bin/env python
"""Probe Mistral models available to the current API key.

Usage:
    set MISTRAL_API_KEY=...
    c:/projects/AIAT/.venv/Scripts/python.exe mas/packages/mas-core/scripts/test_mistral_models.py

Optional env vars:
    MISTRAL_BASE_URL=https://api.mistral.ai
    MAX_MODELS=999
    TIMEOUT_S=30
"""

from __future__ import annotations

import json
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx


def _load_env_value(name: str) -> str:
    value = os.getenv(name, "")
    if value:
        return value

    roots = [
        Path.cwd(),
        Path(__file__).resolve().parent,
    ]
    for root in roots:
        current = root
        for _ in range(8):
            candidate = current / ".env"
            if candidate.is_file():
                for line in candidate.read_text(encoding="utf-8").splitlines():
                    if line.startswith(f"{name}="):
                        return line.split("=", 1)[1].strip()
            if current.parent == current:
                break
            current = current.parent
    return ""


BASE_URL = os.getenv("MISTRAL_BASE_URL", "https://api.mistral.ai").rstrip("/")
API_KEY = _load_env_value("MISTRAL_API_KEY")
MAX_MODELS = int(os.getenv("MAX_MODELS", "999"))
TIMEOUT_S = int(os.getenv("TIMEOUT_S", "30"))

if not API_KEY:
    print("ERROR: MISTRAL_API_KEY is not set.", file=sys.stderr)
    sys.exit(1)

HEADERS = {
    "Authorization": f"Bearer {API_KEY}",
    "Content-Type": "application/json",
}

MODELS_URL = f"{BASE_URL}/v1/models"
CHAT_URL = f"{BASE_URL}/v1/chat/completions"
EMBED_URL = f"{BASE_URL}/v1/embeddings"
MOD_URL = f"{BASE_URL}/v1/moderations"
OCR_URL = f"{BASE_URL}/v1/ocr"


@dataclass
class TestResult:
    model_id: str
    kind: str
    ok: bool
    status: int | None
    ms: int
    note: str


def post_json(
    client: httpx.Client,
    url: str,
    payload: dict[str, Any],
    *,
    max_retries: int = 3,
) -> tuple[int | None, dict[str, Any], str, int]:
    """POST JSON with simple exponential backoff for 429/5xx."""
    delay = 1.0
    last_text = ""
    start = time.time()

    for attempt in range(1, max_retries + 1):
        try:
            response = client.post(url, headers=HEADERS, json=payload, timeout=TIMEOUT_S)
            last_text = response.text

            if response.status_code in (429, 500, 502, 503, 504) and attempt < max_retries:
                time.sleep(delay)
                delay *= 2
                continue

            try:
                data = response.json()
            except ValueError:
                data = {}

            ms = int((time.time() - start) * 1000)
            return response.status_code, data, last_text, ms
        except httpx.HTTPError as exc:
            last_text = str(exc)
            if attempt < max_retries:
                time.sleep(delay)
                delay *= 2
                continue
            ms = int((time.time() - start) * 1000)
            return None, {}, last_text, ms

    ms = int((time.time() - start) * 1000)
    return None, {}, last_text, ms


def list_models(client: httpx.Client) -> list[dict[str, Any]]:
    response = client.get(MODELS_URL, headers=HEADERS, timeout=TIMEOUT_S)
    response.raise_for_status()
    return response.json().get("data", [])


def is_embedding_model(model_id: str) -> bool:
    low = model_id.lower()
    return model_id == "mistral-embed" or "embed" in low


def is_moderation_model(model_id: str) -> bool:
    return model_id.lower().startswith("mistral-moderation")


def is_ocr_model(model_id: str) -> bool:
    return "ocr" in model_id.lower()


def test_chat(client: httpx.Client, model_id: str) -> TestResult:
    payload = {
        "model": model_id,
        "messages": [{"role": "user", "content": "Reply with exactly: OK"}],
        "max_tokens": 8,
        "temperature": 0,
    }
    status, data, raw, ms = post_json(client, CHAT_URL, payload)
    if status == 200:
        try:
            content = data["choices"][0]["message"]["content"]
        except Exception:
            content = ""
        return TestResult(model_id, "chat", True, status, ms, f"content={content!r}")
    return TestResult(model_id, "chat", False, status, ms, raw[:200].replace("\n", " "))


def test_embed(client: httpx.Client, model_id: str) -> TestResult:
    status, data, raw, ms = post_json(
        client,
        EMBED_URL,
        {"model": model_id, "input": "hello world"},
    )
    if status == 200:
        try:
            dim = len(data["data"][0]["embedding"])
        except Exception:
            dim = -1
        return TestResult(model_id, "embed", True, status, ms, f"dim={dim}")
    return TestResult(model_id, "embed", False, status, ms, raw[:200].replace("\n", " "))


def test_moderation(client: httpx.Client, model_id: str) -> TestResult:
    status, _, raw, ms = post_json(
        client,
        MOD_URL,
        {"model": model_id, "input": "hello world"},
    )
    if status == 200:
        return TestResult(model_id, "moderation", True, status, ms, "OK")
    return TestResult(model_id, "moderation", False, status, ms, raw[:200].replace("\n", " "))


def test_ocr(client: httpx.Client, model_id: str) -> TestResult:
    status, _, raw, ms = post_json(
        client,
        OCR_URL,
        {
            "model": model_id,
            "document": {
                "type": "document_url",
                "document_url": "https://arxiv.org/pdf/2201.04234",
            },
        },
    )
    if status == 200:
        return TestResult(model_id, "ocr", True, status, ms, "OK")
    return TestResult(model_id, "ocr", False, status, ms, raw[:200].replace("\n", " "))


def main() -> None:
    with httpx.Client() as client:
        models = list_models(client)
        ids = []
        seen = set()
        for item in models:
            model_id = item.get("id", "")
            if model_id and model_id not in seen:
                seen.add(model_id)
                ids.append(model_id)
        ids = ids[:MAX_MODELS]

        print(f"Discovered {len(ids)} model(s) from {MODELS_URL}\n")

        results: list[TestResult] = []
        for i, model_id in enumerate(ids, 1):
            if is_embedding_model(model_id):
                result = test_embed(client, model_id)
            elif is_moderation_model(model_id):
                result = test_moderation(client, model_id)
            elif is_ocr_model(model_id):
                result = test_ocr(client, model_id)
            else:
                result = test_chat(client, model_id)

            results.append(result)
            status_str = result.status if result.status is not None else "-"
            ok = "OK" if result.ok else "FAIL"
            print(
                f"[{i:02d}/{len(ids):02d}] {model_id:32} "
                f"{result.kind:10} {ok:4} status={status_str} {result.ms}ms  {result.note}"
            )
            time.sleep(0.2)

    ok_count = sum(1 for result in results if result.ok)
    failed = [result for result in results if not result.ok]

    print("\n--- Summary ---")
    print(f"Total: {len(results)} | OK: {ok_count} | FAIL: {len(failed)}")

    if failed:
        print("\nFailed models:")
        for result in failed:
            print(
                f"- {result.model_id} ({result.kind}) "
                f"status={result.status} note={result.note}"
            )

    print("\nOK model IDs:")
    print(json.dumps([result.model_id for result in results if result.ok], indent=2))


if __name__ == "__main__":
    main()

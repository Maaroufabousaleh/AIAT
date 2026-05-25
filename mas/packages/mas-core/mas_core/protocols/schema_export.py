"""JSON schema export helpers for AIAT protocol contracts."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .envelope import MessageEnvelope
from .tool import ToolRequest, ToolResponse
from .worker_manifest import WorkerManifest

PROTOCOL_SCHEMA_VERSION = "aiat.v1"

SCHEMA_MODELS = {
    "MessageEnvelope": MessageEnvelope,
    "ToolRequest": ToolRequest,
    "ToolResponse": ToolResponse,
    "WorkerManifest": WorkerManifest,
}


def protocol_schema_bundle() -> dict[str, Any]:
    """Return JSON schemas for the public AIAT v1 wire contracts."""
    return {
        "protocol_version": PROTOCOL_SCHEMA_VERSION,
        "schemas": {
            name: model.model_json_schema(mode="serialization")
            for name, model in SCHEMA_MODELS.items()
        },
    }


def write_protocol_schema_bundle(path: str | Path) -> None:
    """Write the AIAT v1 schema bundle to ``path``."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(protocol_schema_bundle(), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

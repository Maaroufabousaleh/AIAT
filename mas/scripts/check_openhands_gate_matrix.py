"""Evaluate the OpenHands mandatory gate matrix fail-closed."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

try:
    from openhands_certification_gates import GATE_DEFINITIONS, evaluate_gate_map, initial_gate_map
except ImportError:  # pragma: no cover - package invocation fallback
    from scripts.openhands_certification_gates import (  # type: ignore
        GATE_DEFINITIONS,
        evaluate_gate_map,
        initial_gate_map,
    )

SCHEMA = "aiat.openhands-certification-gate-evaluation.v1"


def _load_gate_rows(path: Path | None) -> dict[str, dict[str, Any]]:
    gates = initial_gate_map()
    if path is None or not path.is_file():
        return gates
    payload = json.loads(path.read_text(encoding="utf-8"))
    source = payload.get("gates") if isinstance(payload, dict) else payload
    if isinstance(source, list):
        source = {str(row.get("gate_id")): row for row in source if isinstance(row, dict) and row.get("gate_id")}
    if isinstance(source, dict):
        for gate_id, row in source.items():
            if gate_id in gates and isinstance(row, dict):
                gates[gate_id] = {**gates[gate_id], **row}
    return gates


def evaluate(
    *,
    gate_status_path: Path | None = None,
    provider_status: str | None = None,
    candidate_sha: str | None = None,
    source_commit: str | None = None,
    image_digest: str | None = None,
) -> dict[str, Any]:
    gates = _load_gate_rows(gate_status_path)
    result = evaluate_gate_map(gates, blocker_status=provider_status)
    return {
        "schema_version": SCHEMA,
        "status": result["status"],
        "candidate_sha": candidate_sha,
        "openhands_source_commit": source_commit,
        "openhands_image_digest": image_digest,
        "gates": gates,
        "evaluation": result,
        "provider_configuration_status": provider_status,
        "payloads_retained": False,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--gate-status", type=Path)
    parser.add_argument("--provider-status")
    parser.add_argument("--candidate-sha")
    parser.add_argument("--source-commit")
    parser.add_argument("--image-digest")
    args = parser.parse_args(argv)
    report = evaluate(
        gate_status_path=args.gate_status,
        provider_status=args.provider_status,
        candidate_sha=args.candidate_sha,
        source_commit=args.source_commit,
        image_digest=args.image_digest,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": report["status"], "mandatory_gate_count": len(GATE_DEFINITIONS)}, sort_keys=True))
    return 0 if report["status"] == "PASSED" else 2


if __name__ == "__main__":
    raise SystemExit(main())

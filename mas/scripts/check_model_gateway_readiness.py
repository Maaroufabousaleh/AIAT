"""Check the AIAT model gateway's declared and read-only route surface.

The checker is deliberately narrower than model dispatch certification. Static
mode reconciles the Compose LiteLLM alias declarations with AIAT's runtime
registry. ``--live`` performs one authenticated ``GET /v1/models`` request and
checks only the bounded model IDs returned by the gateway. It never sends a
completion, creates a profile, changes routing, activates a worker, or calls a
provider. Licence and restriction metadata are outside this operational check.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx
import yaml

from mas_core.llm_gateway import MODEL_REGISTRY

MAS_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = MAS_ROOT / "infra" / "compose" / "litellm_config.yaml"
SCHEMA = "aiat.model-gateway-readiness.v1"
EXPECTED_ALIASES = (
    "auto",
    "omniroute-auto",
    "omniroute-free",
    "omniroute-coding",
    "omniroute-smart",
)


def _configured_url() -> str:
    explicit = os.getenv("AIAT_LIVE_LLM_GATEWAY_URL", "").strip()
    if explicit:
        return explicit.rstrip("/")
    configured = os.getenv("AIAT_ORCHESTRATOR_URL", os.getenv("ORCHESTRATOR_API_URL", "")).strip()
    if not configured:
        configured = os.getenv("LLM_GATEWAY_URL", "").strip()
    if not configured:
        return ""
    parsed = urlparse(configured)
    if parsed.hostname in {"litellm", "omniroute"}:
        # Compose service names are not host-reachable from the operator shell.
        return "http://127.0.0.1:8000"
    return configured.rstrip("/")


def _declared_aliases(config: dict[str, Any]) -> set[str]:
    model_list = config.get("model_list")
    if not isinstance(model_list, list):
        raise ValueError("model_list must be a list")
    aliases: set[str] = set()
    for row in model_list:
        if not isinstance(row, dict):
            raise ValueError("model_list entries must be mappings")
        name = row.get("model_name")
        if isinstance(name, str) and name.strip():
            aliases.add(name.strip())
    return aliases


def inspect_static(*, config_path: Path = DEFAULT_CONFIG) -> dict[str, Any]:
    """Reconcile checked-in alias declarations without making network calls."""
    try:
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError("LiteLLM config must be a mapping")
        declared = _declared_aliases(raw)
    except (OSError, ValueError, yaml.YAMLError) as exc:
        return {
            "schema_version": SCHEMA,
            "mode": "static",
            "status": "fail",
            "reason": f"invalid LiteLLM alias configuration ({type(exc).__name__})",
            "config_path": str(config_path),
            "declared_aliases": [],
            "missing_aliases": list(EXPECTED_ALIASES),
            "registry_alias_present": False,
            "dispatch_performed": False,
            "provider_call_performed": False,
        }

    missing = sorted(set(EXPECTED_ALIASES) - declared)
    registry_alias_present = MODEL_REGISTRY.get("omniroute-coding") is not None
    errors: list[str] = []
    if missing:
        errors.append("missing expected LiteLLM aliases")
    if not registry_alias_present:
        errors.append("omniroute-coding is missing from the AIAT runtime registry")
    return {
        "schema_version": SCHEMA,
        "mode": "static",
        "status": "pass" if not errors else "fail",
        "reason": None if not errors else "; ".join(errors),
        "config_path": str(config_path),
        "declared_aliases": sorted(declared),
        "expected_aliases": list(EXPECTED_ALIASES),
        "missing_aliases": missing,
        "registry_alias_present": registry_alias_present,
        "dispatch_performed": False,
        "provider_call_performed": False,
        "mutations_performed": False,
        "licence_metadata_is_gate": False,
    }


def inspect_live(*, url: str, api_key: str, timeout: float = 10.0) -> dict[str, Any]:
    """Read the gateway model listing without issuing a completion request."""
    base_url = url.strip().rstrip("/")
    if not base_url:
        return {
            "schema_version": SCHEMA,
            "mode": "live",
            "status": "blocked",
            "reason": "missing live gateway URL",
            "dispatch_performed": False,
            "provider_call_performed": False,
        }
    headers = {"Authorization": f"Bearer {api_key.strip()}"} if api_key.strip() else {}
    try:
        response = httpx.get(f"{base_url}/v1/models", headers=headers, timeout=timeout)
        response.raise_for_status()
        payload = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        return {
            "schema_version": SCHEMA,
            "mode": "live",
            "status": "blocked",
            "reason": f"gateway model listing unavailable ({type(exc).__name__})",
            "dispatch_performed": False,
            "provider_call_performed": False,
        }
    if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
        return {
            "schema_version": SCHEMA,
            "mode": "live",
            "status": "blocked",
            "reason": "gateway model listing returned malformed JSON",
            "dispatch_performed": False,
            "provider_call_performed": False,
        }
    model_ids = {
        str(row.get("id")).strip()
        for row in payload["data"]
        if isinstance(row, dict) and isinstance(row.get("id"), str) and row.get("id", "").strip()
    }
    missing = sorted(set(EXPECTED_ALIASES) - model_ids)
    return {
        "schema_version": SCHEMA,
        "mode": "live",
        "status": "pass" if not missing else "fail",
        "reason": None if not missing else "gateway is missing expected AIAT aliases",
        "endpoint": "/v1/models",
        "model_count": len(model_ids),
        "expected_aliases": list(EXPECTED_ALIASES),
        "available_aliases": sorted(set(EXPECTED_ALIASES) & model_ids),
        "missing_aliases": missing,
        "dispatch_performed": False,
        "provider_call_performed": False,
        "mutations_performed": False,
        "licence_metadata_is_gate": False,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--live", action="store_true", help="read GET /v1/models from a running gateway")
    parser.add_argument("--url", default=_configured_url(), help="gateway URL or AIAT_LIVE_LLM_GATEWAY_URL")
    parser.add_argument(
        "--api-key",
        default=os.getenv("AIAT_LIVE_LLM_API_KEY", os.getenv("MAS_API_KEY", "")),
        help="optional bearer key; never included in the report",
    )
    parser.add_argument("--timeout", type=float, default=10.0)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    static = inspect_static(config_path=args.config)
    report: dict[str, Any] = static
    if args.live:
        report = {**static, "live": inspect_live(url=args.url, api_key=args.api_key, timeout=args.timeout)}
    if args.json:
        print(json.dumps(report, sort_keys=True, indent=2))
    else:
        print(f"model gateway readiness: static={static['status']}")
        if args.live:
            live = report["live"]
            print(f"live={live['status']} — {live.get('reason') or 'all expected aliases present'}")
    if static["status"] != "pass":
        return 1
    if not args.live:
        return 0
    return 0 if report["live"]["status"] == "pass" else (2 if report["live"]["status"] == "blocked" else 1)


if __name__ == "__main__":
    sys.exit(main())

"""Check provider configuration without reading or retaining secret values."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

try:
    from openhands_gateway_errors import classify_failure
except ImportError:  # pragma: no cover - package invocation fallback
    from scripts.openhands_gateway_errors import classify_failure  # type: ignore

SCHEMA = "aiat.openhands-certification-provider-preflight.v1"
PROVIDER = "groq"
SECRET_NAME = "GROQ_API_KEY"


def check_provider_configuration(value: str | None) -> dict[str, object]:
    """Return scalar readiness evidence; never include ``value`` itself."""

    configured = bool((value or "").strip())
    if configured:
        return {
            "schema_version": SCHEMA,
            "status": "PASS",
            "provider_configuration_status": "CONFIGURED",
            "provider": PROVIDER,
            "secret_name": SECRET_NAME,
            "secret_present": True,
            "secret_value_retained": False,
        }
    failure = classify_failure(stage="provider_preflight", provider_secret_present=False)
    return {
        "schema_version": SCHEMA,
        "status": "BLOCKED_MISSING_OPERATOR_SECRET",
        "provider_configuration_status": "BLOCKED_MISSING_OPERATOR_SECRET",
        "provider": PROVIDER,
        "secret_name": SECRET_NAME,
        "secret_present": False,
        "secret_value_retained": False,
        "failure": failure.as_dict(),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    report = check_provider_configuration(os.getenv(SECRET_NAME))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": report["status"], "provider": PROVIDER, "secret_name": SECRET_NAME}, sort_keys=True))
    return 0 if report["status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())

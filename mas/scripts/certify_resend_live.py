"""Run a bounded, secret-safe Resend readiness or transport certificate.

This command is an operator preflight for the provider adapter.  It is not a
replacement for the governed identity-service outbound path and never changes
the outbound certification latch.  Credentials are read only from the
environment; the optional send requires an explicit flag, an operator-owned
recipient in ``AIAT_CERTIFICATION_RECIPIENT``, and performs one request with no
automatic retry.

Examples::

    uv run python scripts/certify_resend_live.py --json readiness
    AIAT_CERTIFICATION_RECIPIENT=operator@example.net \
      AIAT_CERTIFICATION_SENDER=certification@agents.aiat.ca \
      AIAT_CERTIFICATION_IDEMPOTENCY_KEY=resend-certification-2026-09-12 \
      uv run python scripts/certify_resend_live.py --json send-once --confirm-send

The recipient and sender are intentionally environment inputs rather than
command-line values.  Output contains only status, bounded metadata, and an
opaque provider message identifier; it never contains message content,
credentials, or provider response bodies.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

MAS_ROOT = Path(__file__).resolve().parents[1]
IDENTITY_ROOT = MAS_ROOT / "apps" / "identity-service"
if str(IDENTITY_ROOT) not in sys.path:
    sys.path.insert(0, str(IDENTITY_ROOT))

from identity_service.providers.resend import (  # noqa: E402
    ResendAdapterError,
    ResendRelayAdapter,
)

CHECK_SCHEMA = "aiat.resend-live-certification.v1"
PRODUCTION_DOMAIN = "agents.aiat.ca"
_EMAIL_RE = re.compile(r"^[^@\s<>\x00-\x1f\x7f]+@[^@\s<>\x00-\x1f\x7f]+$")
_REFERENCE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/+-]{0,199}$")
_CERTIFICATION_SUBJECT = "AIAT outbound certification"
_CERTIFICATION_BODY = "This is an AIAT mail transport certification message."


class CertificationInputError(ValueError):
    """Safe operator-input or configuration failure."""


def _required_environment(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise CertificationInputError(f"required environment variable is missing: {name}")
    return value


def _email_environment(name: str, *, expected_domain: str | None = None) -> str:
    value = _required_environment(name)
    if len(value) > 320 or not _EMAIL_RE.fullmatch(value):
        raise CertificationInputError(f"{name} is invalid")
    if expected_domain and value.rsplit("@", 1)[1].casefold() != expected_domain:
        raise CertificationInputError(f"{name} must use the configured production domain")
    return value


def _safe_reference(value: str, name: str) -> str:
    candidate = str(value or "").strip()
    if not _REFERENCE_RE.fullmatch(candidate):
        raise CertificationInputError(f"{name} is missing or invalid")
    return candidate


def _webhook_secret_is_configured(secret: str) -> bool:
    encoded = str(secret or "").strip()
    if encoded.startswith("whsec_"):
        encoded = encoded[len("whsec_") :]
    if not encoded:
        return False
    try:
        decoded = base64.b64decode(encoded.encode("ascii"), altchars=b"-_", validate=True)
    except (ValueError, UnicodeEncodeError):
        return False
    return bool(decoded)


def _dns_probe(record_type: str, name: str) -> dict[str, Any]:
    """Return DNS presence only; never return record values."""

    if shutil.which("dig") is None:
        return {"available": False, "present": None, "record_count": 0}
    try:
        result = subprocess.run(
            ["dig", "+short", record_type, name],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return {"available": True, "present": False, "record_count": 0}
    records = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    return {
        "available": True,
        "present": bool(records) and result.returncode == 0,
        "record_count": min(len(records), 100),
    }


def _dns_summary(domain: str) -> dict[str, Any]:
    return {
        "domain_txt": _dns_probe("TXT", domain),
        "domain_mx": _dns_probe("MX", domain),
        "dmarc_txt": _dns_probe("TXT", f"_dmarc.{domain}"),
    }


def _adapter_from_environment(timeout_seconds: float) -> ResendRelayAdapter:
    if timeout_seconds < 1 or timeout_seconds > 120:
        raise CertificationInputError("timeout must be between 1 and 120 seconds")
    api_key = _required_environment("RESEND_API_KEY")
    webhook_secret = _required_environment("RESEND_WEBHOOK_SIGNING_SECRET")
    try:
        return ResendRelayAdapter(
            api_key=api_key,
            sending_domain=PRODUCTION_DOMAIN,
            timeout_seconds=timeout_seconds,
            webhook_signing_secret=webhook_secret,
        )
    except (TypeError, ValueError) as exc:
        raise CertificationInputError("Resend configuration is invalid") from exc


def _base_report(operation: str) -> dict[str, Any]:
    return {
        "schema_version": CHECK_SCHEMA,
        "provider": "resend",
        "mode": "live",
        "operation": operation,
        "outbound_relay_certified_changed": False,
    }


async def _readiness(adapter: ResendRelayAdapter) -> dict[str, Any]:
    report = _base_report("readiness")
    auth = await adapter.validate_relay_credentials()
    domain = await adapter.validate_sending_domain()
    webhook_configured = _webhook_secret_is_configured(adapter._webhook_signing_secret)
    dns = _dns_summary(adapter.sending_domain)
    dns_available = all(item["available"] for item in dns.values())
    report.update(
        {
            "api_auth": "PASS" if auth.get("valid") else "FAIL",
            "sending_domain": adapter.sending_domain,
            "domain_status_accepted": bool(domain.get("valid")),
            "domain_reference_present": bool(domain.get("domain_id")),
            "webhook_secret_format": "PASS" if webhook_configured else "FAIL",
            "public_dns": dns,
            "public_dns_probe": "PASS" if dns_available else "NOT_RUN",
        }
    )
    report["status"] = (
        "PASS"
        if auth.get("valid") and domain.get("valid") and webhook_configured
        else "FAIL"
    )
    return report


async def execute_command(
    args: argparse.Namespace, adapter: ResendRelayAdapter
) -> dict[str, Any]:
    """Execute a selected command using an injected adapter for tests."""

    if args.command == "readiness":
        return await _readiness(adapter)

    if args.command == "send-once":
        if not args.confirm_send:
            raise CertificationInputError(
                "send-once requires --confirm-send; no provider request was made"
            )
        readiness = await _readiness(adapter)
        if readiness.get("status") != "PASS":
            readiness["operation"] = "send-once"
            readiness["send_skipped"] = True
            return readiness
        recipient = _email_environment("AIAT_CERTIFICATION_RECIPIENT")
        sender = _email_environment(
            "AIAT_CERTIFICATION_SENDER", expected_domain=PRODUCTION_DOMAIN
        )
        idempotency_key = _safe_reference(
            _required_environment("AIAT_CERTIFICATION_IDEMPOTENCY_KEY"),
            "AIAT_CERTIFICATION_IDEMPOTENCY_KEY",
        )
        result = await adapter.send_message(
            sender=sender,
            recipients=[recipient],
            subject=_CERTIFICATION_SUBJECT,
            body=_CERTIFICATION_BODY,
            idempotency_key=idempotency_key,
            content_type="text/plain",
        )
        provider_message_id = _safe_reference(
            result.get("provider_message_id"), "provider_message_id"
        )
        report = _base_report("send-once")
        report.update(
            {
                "status": "PASS",
                "send_count": 1,
                "provider_message_id": provider_message_id,
                "correlation_id": str(result.get("correlation_id") or ""),
                "recipient_configured": True,
                "sender_configured": True,
                "recipient_sha256": hashlib.sha256(recipient.encode()).hexdigest(),
                "automatic_retry": False,
                "governed_identity_service_path": False,
            }
        )
        return report

    raise CertificationInputError("unsupported certification command")


async def _run(args: argparse.Namespace) -> dict[str, Any]:
    adapter = _adapter_from_environment(args.timeout)
    try:
        return await execute_command(args, adapter)
    finally:
        closer = getattr(adapter, "aclose", None)
        if callable(closer):
            await closer()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run a bounded, secret-safe live Resend provider certificate."
    )
    parser.add_argument("--json", action="store_true", help="emit JSON evidence")
    parser.add_argument(
        "--timeout", type=float, default=15.0, help="per-request timeout, 1-120 seconds"
    )
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("readiness", help="validate API access, domain, DNS probes, and webhook secret format")
    send = commands.add_parser(
        "send-once",
        help="send one explicit transport probe; this is not the governed identity path",
    )
    send.add_argument(
        "--confirm-send",
        action="store_true",
        help="explicitly authorize the single harmless certification send",
    )
    return parser


def _failure_report(args: argparse.Namespace, exc: Exception) -> dict[str, Any]:
    report = _base_report(str(args.command))
    report["status"] = "FAIL"
    if isinstance(exc, ResendAdapterError):
        report.update(
            {
                "error_code": exc.code,
                "transient": exc.transient,
                "correlation_id": exc.correlation_id,
                "network_access_performed": True,
            }
        )
    elif isinstance(exc, CertificationInputError):
        report["error_code"] = "CERTIFICATION_INPUT_INVALID"
    else:
        report.update({"error_code": "CERTIFICATION_FAILED", "error_type": type(exc).__name__})
    return report


def _print_report(report: dict[str, Any], *, as_json: bool) -> None:
    if as_json:
        print(json.dumps(report, sort_keys=True, indent=2))
        return
    print(f"resend live certification: {report.get('status', 'FAIL')}")
    for key in (
        "api_auth",
        "sending_domain",
        "domain_status_accepted",
        "webhook_secret_format",
        "public_dns_probe",
        "send_count",
        "provider_message_id",
        "correlation_id",
        "error_code",
    ):
        if key in report:
            print(f"{key}={json.dumps(report[key], sort_keys=True)}")


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    try:
        report = asyncio.run(_run(args))
    except Exception as exc:  # pragma: no cover - final safe CLI boundary
        report = _failure_report(args, exc)
    _print_report(report, as_json=args.json)
    return 0 if report.get("status") == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())

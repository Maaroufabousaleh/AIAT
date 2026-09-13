"""Run a repeatable, payload-free live certificate for Cloudflare inbound mail.

The script uses the same ``CloudflareInboundAdapter`` and HMAC request
implementation as identity-service. It performs only the explicitly selected
signed mail-edge API operation; it never sends an external message and never
calls Cloudflare account administration APIs. Secrets are read only from the
environment and are never printed.

Examples::

    uv run python scripts/certify_cloudflare_mail_edge_live.py health
    uv run python scripts/certify_cloudflare_mail_edge_live.py register-smoke-recipient \
        --recipient w-live-smoke@agents.aiat.ca --identity-id smoke-identity \
        --worker-id smoke-worker
    uv run python scripts/certify_cloudflare_mail_edge_live.py inspect-events \
        --recipient w-live-smoke@agents.aiat.ca
    uv run python scripts/certify_cloudflare_mail_edge_live.py fetch-and-validate-message \
        --recipient w-live-smoke@agents.aiat.ca --identity-id smoke-identity \
        --worker-id smoke-worker --message-id <message-id> \
        --expected-subject "AIAT live inbound smoke test"
    uv run python scripts/certify_cloudflare_mail_edge_live.py finalize-certification \
        --recipient w-live-smoke@agents.aiat.ca --identity-id smoke-identity \
        --provider-reference <provider-reference>

The production environment must provide ``CLOUDFLARE_MAIL_EDGE_URL`` and
``CLOUDFLARE_MAIL_EDGE_AUTH_SECRET``. The provider reference is an opaque,
non-secret value returned by registration and is intentionally required for
lifecycle cleanup rather than inferred from a local database.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

MAS_ROOT = Path(__file__).resolve().parents[1]
IDENTITY_ROOT = MAS_ROOT / "apps" / "identity-service"
if str(IDENTITY_ROOT) not in sys.path:
    sys.path.insert(0, str(IDENTITY_ROOT))

from identity_service.providers.inbound.cloudflare import (  # noqa: E402
    CloudflareInboundAdapter,
    CloudflareProviderError,
    normalize_email_address,
)

CHECK_SCHEMA = "aiat.cloudflare-mail-edge-live-certification.v1"
PRODUCTION_MAIL_DOMAIN = "agents.aiat.ca"
_SAFE_REFERENCE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/+-]{0,199}$")


class CertificationInputError(ValueError):
    """Safe operator-input/configuration failure."""


def _safe_reference(value: str, label: str) -> str:
    candidate = str(value or "").strip()
    if not _SAFE_REFERENCE_RE.fullmatch(candidate):
        raise CertificationInputError(f"{label} is missing or invalid")
    return candidate


def _production_recipient(value: str) -> str:
    try:
        recipient = normalize_email_address(value)
    except ValueError as exc:
        raise CertificationInputError("recipient is missing or invalid") from exc
    if not recipient.endswith(f"@{PRODUCTION_MAIL_DOMAIN}"):
        raise CertificationInputError(
            f"live Cloudflare certification only permits @{PRODUCTION_MAIL_DOMAIN} recipients"
        )
    return recipient


def _required_environment(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise CertificationInputError(f"required environment variable is missing: {name}")
    return value


def _adapter_from_environment(timeout_seconds: float) -> CloudflareInboundAdapter:
    if timeout_seconds < 1 or timeout_seconds > 120:
        raise CertificationInputError("timeout must be between 1 and 120 seconds")
    edge_url = _required_environment("CLOUDFLARE_MAIL_EDGE_URL")
    auth_secret = _required_environment("CLOUDFLARE_MAIL_EDGE_AUTH_SECRET")
    if len(auth_secret) < 20:
        raise CertificationInputError(
            "CLOUDFLARE_MAIL_EDGE_AUTH_SECRET is below the minimum length"
        )
    try:
        return CloudflareInboundAdapter(
            edge_url=edge_url,
            auth_secret=auth_secret,
            timeout_seconds=timeout_seconds,
        )
    except ValueError as exc:
        raise CertificationInputError("Cloudflare mail-edge configuration is invalid") from exc


def _base_report(operation: str) -> dict[str, Any]:
    return {
        "schema_version": CHECK_SCHEMA,
        "provider": "cloudflare",
        "mode": "live",
        "operation": operation,
        "network_access_performed": True,
    }


def _safe_event(event: Any) -> dict[str, Any]:
    if not isinstance(event, dict):
        raise CertificationInputError("mail-edge returned an invalid event row")
    safe: dict[str, Any] = {}
    for key in (
        "sequence",
        "event_id",
        "provider_event_id",
        "provider_message_id",
        "message_id",
        "identity_id",
        "worker_id",
        "envelope_recipient",
        "event_type",
        "received_at",
        "raw_size",
    ):
        if key in event:
            safe[key] = event[key]
    return safe


def _safe_message(
    fetched: dict[str, Any],
    *,
    include_subject: bool,
) -> dict[str, Any]:
    raw = fetched.get("raw_mime")
    if not isinstance(raw, bytes):
        raise CertificationInputError("adapter did not reconstruct a binary message")
    view = fetched.get("message")
    if not isinstance(view, dict):
        raise CertificationInputError("adapter did not parse a message view")
    result: dict[str, Any] = {
        "message_id": str(fetched.get("message_id") or ""),
        "provider_event_id": str(fetched.get("provider_event_id") or ""),
        "identity_id": str(fetched.get("identity_id") or ""),
        "worker_id": str(fetched.get("worker_id") or ""),
        "envelope_recipient": str(view.get("envelope_recipient") or ""),
        "raw_size": len(raw),
        "mime_parsed": True,
        "raw_mime_reconstructed": True,
    }
    if include_subject:
        result["subject"] = str(view.get("subject") or "")
    return result


async def execute_command(
    args: argparse.Namespace, adapter: CloudflareInboundAdapter
) -> dict[str, Any]:
    """Execute one selected command using an already-created real adapter.

    Keeping the adapter as an argument makes deterministic MockTransport tests
    possible without changing the production command's provider path.
    """

    command = str(args.command)
    report = _base_report("finalize-certification" if command == "cleanup" else command)

    if command == "health":
        result = await adapter.health_check()
        report.update(
            {
                "status": "PASS" if result.get("healthy") else "FAIL",
                "health": bool(result.get("healthy")),
            }
        )
        return report

    if command == "register-smoke-recipient":
        recipient = _production_recipient(args.recipient)
        identity_id = _safe_reference(args.identity_id, "identity_id")
        worker_id = _safe_reference(args.worker_id, "worker_id")
        idempotency_key = _safe_reference(
            args.idempotency_key or f"live-certification:{recipient.replace('@', ':')}",
            "idempotency_key",
        )
        result = await adapter.provision_identity(
            recipient,
            identity_id=identity_id,
            worker_id=worker_id,
            idempotency_key=idempotency_key,
        )
        provider_reference = _safe_reference(result.get("provider_reference"), "provider_reference")
        report.update(
            {
                "status": "PASS",
                "recipient": recipient,
                "identity_id": identity_id,
                "worker_id": worker_id,
                "provider_reference": provider_reference,
                "correlation_id": str(result.get("correlation_id") or ""),
            }
        )
        return report

    if command == "inspect-events":
        recipient = _production_recipient(args.recipient) if args.recipient else None
        identity_id = _safe_reference(args.identity_id, "identity_id") if args.identity_id else None
        worker_id = _safe_reference(args.worker_id, "worker_id") if args.worker_id else None
        result = await adapter.list_events(after=args.after, limit=args.limit)
        events = result.get("events")
        if not isinstance(events, list):
            raise CertificationInputError("mail-edge returned an invalid event list")
        selected = []
        for event in events:
            safe_event = _safe_event(event)
            if recipient and safe_event.get("envelope_recipient") != recipient:
                continue
            if identity_id and str(safe_event.get("identity_id") or "") != identity_id:
                continue
            if worker_id and str(safe_event.get("worker_id") or "") != worker_id:
                continue
            selected.append(safe_event)
        report.update(
            {
                "status": "PASS",
                "cursor": result.get("cursor"),
                "next_cursor": result.get("next_cursor"),
                "event_count": len(selected),
                "events": selected,
            }
        )
        if recipient:
            report["recipient"] = recipient
        if identity_id:
            report["identity_id"] = identity_id
        if worker_id:
            report["worker_id"] = worker_id
        return report

    if command == "fetch-and-validate-message":
        recipient = _production_recipient(args.recipient)
        identity_id = _safe_reference(args.identity_id, "identity_id")
        worker_id = _safe_reference(args.worker_id, "worker_id")
        message_id = _safe_reference(args.message_id, "message_id")
        fetched = await adapter.fetch_message(message_id)
        safe_message = _safe_message(fetched, include_subject=args.include_subject)
        checks = {
            "identity_id_matches": safe_message["identity_id"] == identity_id,
            "worker_id_matches": safe_message["worker_id"] == worker_id,
            "envelope_recipient_matches": safe_message["envelope_recipient"] == recipient,
            "raw_mime_reconstructed": safe_message["raw_mime_reconstructed"] is True,
            "mime_parsed": safe_message["mime_parsed"] is True,
        }
        if args.expected_subject is not None:
            # The expected value is used only for comparison. It is not copied
            # into the report, so a normal run cannot echo message content.
            fetched_view = fetched.get("message")
            checks["subject_matches"] = (
                isinstance(fetched_view, dict)
                and str(fetched_view.get("subject") or "") == args.expected_subject
            )
        report.update(
            {
                "status": "PASS" if all(checks.values()) else "FAIL",
                "recipient": recipient,
                "identity_id": identity_id,
                "worker_id": worker_id,
                "message": safe_message,
                "checks": checks,
            }
        )
        return report

    if command in {"activate", "suspend", "retire", "cleanup", "finalize-certification"}:
        recipient = _production_recipient(args.recipient)
        identity_id = _safe_reference(args.identity_id, "identity_id")
        worker_id = _safe_reference(args.worker_id, "worker_id")
        provider_reference = _safe_reference(args.provider_reference, "provider_reference")
        lifecycle_action = "retire" if command in {"cleanup", "finalize-certification"} else command
        lifecycle = getattr(adapter, f"{lifecycle_action}_identity")
        result = await lifecycle(
            provider_reference,
            identity_id=identity_id,
            address=recipient,
        )
        nested = result.get("result") if isinstance(result.get("result"), dict) else {}
        state = str(nested.get("state") or "")
        report.update(
            {
                "status": "PASS"
                if state
                == {"activate": "ACTIVE", "suspend": "SUSPENDED", "retire": "RETIRED"}[
                    lifecycle_action
                ]
                else "FAIL",
                "recipient": recipient,
                "identity_id": identity_id,
                "worker_id": worker_id,
                "provider_reference": provider_reference,
                "lifecycle_state": state,
                "correlation_id": str(result.get("correlation_id") or ""),
            }
        )
        return report

    raise CertificationInputError("unsupported certification command")


async def _run(args: argparse.Namespace) -> dict[str, Any]:
    adapter = _adapter_from_environment(args.timeout)
    try:
        return await execute_command(args, adapter)
    finally:
        await adapter.aclose()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run a repeatable, payload-free live certificate for Cloudflare inbound mail."
    )
    parser.add_argument("--json", action="store_true", help="emit machine-readable safe evidence")
    parser.add_argument(
        "--timeout", type=float, default=15.0, help="per-request timeout, 1-120 seconds"
    )
    commands = parser.add_subparsers(dest="command", required=True)

    commands.add_parser("health", help="run signed /v1/health")

    register = commands.add_parser(
        "register-smoke-recipient", help="idempotently register one exact production recipient"
    )
    register.add_argument("--recipient", required=True)
    register.add_argument("--identity-id", required=True)
    register.add_argument("--worker-id", required=True)
    register.add_argument("--idempotency-key")

    inspect = commands.add_parser("inspect-events", help="list bounded safe event metadata")
    inspect.add_argument("--recipient")
    inspect.add_argument("--identity-id")
    inspect.add_argument("--worker-id")
    inspect.add_argument("--after", type=int, default=0)
    inspect.add_argument("--limit", type=int, default=100)

    fetch = commands.add_parser(
        "fetch-and-validate-message", help="fetch one message and validate safe correlations"
    )
    fetch.add_argument("--recipient", required=True)
    fetch.add_argument("--identity-id", required=True)
    fetch.add_argument("--worker-id", required=True)
    fetch.add_argument("--message-id", required=True)
    fetch.add_argument("--expected-subject")
    fetch.add_argument(
        "--include-subject", action="store_true", help="include the parsed subject in output"
    )

    for action in ("activate", "suspend", "retire"):
        lifecycle = commands.add_parser(action, help=f"signed recipient {action} transition")
        lifecycle.add_argument("--recipient", required=True)
        lifecycle.add_argument("--identity-id", required=True)
        lifecycle.add_argument("--worker-id", required=True)
        lifecycle.add_argument("--provider-reference", required=True)

    cleanup = commands.add_parser(
        "cleanup",
        aliases=["finalize-certification"],
        help="retire the temporary certification recipient through the signed lifecycle API",
    )
    cleanup.add_argument("--recipient", required=True)
    cleanup.add_argument("--identity-id", required=True)
    cleanup.add_argument("--worker-id", required=True)
    cleanup.add_argument("--provider-reference", required=True)
    return parser


def _failure_report(args: argparse.Namespace, exc: Exception) -> dict[str, Any]:
    report = _base_report(str(args.command))
    report["status"] = "FAIL"
    report["network_access_performed"] = isinstance(exc, CloudflareProviderError)
    if isinstance(exc, CloudflareProviderError):
        report.update(
            {
                "error_code": exc.code,
                "transient": exc.transient,
                "correlation_id": exc.correlation_id,
            }
        )
    elif isinstance(exc, CertificationInputError):
        report.update({"error_code": "CERTIFICATION_INPUT_INVALID"})
    else:
        # Do not echo arbitrary exception text: transport/library messages can
        # contain URLs, response fragments, or operator-provided values.
        report.update({"error_code": "CERTIFICATION_FAILED", "error_type": type(exc).__name__})
    return report


def _print_report(report: dict[str, Any], *, as_json: bool) -> None:
    if as_json:
        print(json.dumps(report, sort_keys=True, indent=2))
        return
    print(
        f"cloudflare mail-edge {report.get('operation', 'certification')}: {report.get('status', 'FAIL')}"
    )
    for key in (
        "health",
        "recipient",
        "identity_id",
        "worker_id",
        "provider_reference",
        "event_count",
        "message",
        "lifecycle_state",
        "checks",
        "error_code",
        "correlation_id",
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

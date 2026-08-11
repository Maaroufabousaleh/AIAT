"""Exercise the real outbound-mail lifecycle against in-memory state.

The fixture uses the actual ``IdentityService`` and ``OutboundService`` with
an in-process Stalwart-shaped provider.  It proves that outbound delivery is
approval-paused, idempotent after submission, retryable after a definitive
provider failure, and fail-closed after an ambiguous provider outage.  No
external relay, recipient, or network call is used; licence/restriction data
is metadata and is not a lifecycle predicate.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from contextlib import suppress
from pathlib import Path
from typing import Any
from uuid import UUID

IDENTITY_ROOT = Path(__file__).resolve().parents[1] / "apps" / "identity-service"
if str(IDENTITY_ROOT) not in sys.path:
    sys.path.insert(0, str(IDENTITY_ROOT))

from identity_service.config import IdentitySettings  # noqa: E402
from identity_service.models import IdentityState  # noqa: E402
from identity_service.providers.stalwart import StalwartAdapterError  # noqa: E402
from identity_service.service import AuthenticatedClient, IdentityService  # noqa: E402
from identity_service.store import InMemoryIdentityStore  # noqa: E402

CHECK_SCHEMA = "aiat.outbound-mail-lifecycle.v1"
COMPANY_ID = UUID("00000000-0000-4000-a000-000000000831")
WORKER_ID = UUID("00000000-0000-4000-a000-000000000832")


class FixtureMailboxProvider:
    """Local Stalwart-shaped provider; it never submits to a real relay."""

    def __init__(self) -> None:
        self.submission_count = 0
        self.fail_mode: str | None = None
        self.calls: list[str] = []

    async def find_mailbox(self, _address: str) -> None:
        self.calls.append("find_mailbox")
        return None

    async def create_mailbox(
        self, _address: str, *, quota_mb: int, idempotency_key: str
    ) -> dict[str, str]:
        self.calls.append("create_mailbox")
        return {
            "provider_account_id": "fixture-mailbox-account",
            "correlation_id": "fixture-mailbox-create",
        }

    async def get_mailbox(self, provider_account_id: str) -> dict[str, Any]:
        self.calls.append("get_mailbox")
        return {
            "provider_account_id": provider_account_id,
            "correlation_id": "fixture-mailbox-get",
            "result": {"list": []},
        }

    async def read_message(
        self, _account_id: str, message_id: str
    ) -> dict[str, Any]:
        self.calls.append("read_message")
        return {
            "correlation_id": "fixture-mail-read",
            "result": {"list": [{"id": message_id}]},
        }

    async def add_alias(self, *_args: Any) -> dict[str, str]:
        self.calls.append("add_alias")
        return {"correlation_id": "fixture-alias"}

    async def submit_outbound_message(self, *_args: Any, **_kwargs: Any) -> dict[str, str]:
        self.calls.append("submit_outbound_message")
        self.submission_count += 1
        if self.fail_mode == "definitive":
            raise StalwartAdapterError(
                "STALWART_REJECTED",
                "fixture provider rejected submission",
                correlation_id="fixture-rejected",
            )
        if self.fail_mode == "ambiguous":
            raise StalwartAdapterError(
                "STALWART_UNAVAILABLE",
                "fixture provider became unavailable after accepting the request",
                transient=True,
                correlation_id="fixture-outage",
            )
        return {
            "correlation_id": f"fixture-submit-{self.submission_count}",
            "provider_message_id": f"fixture-message-{self.submission_count}",
        }


def _clients() -> tuple[AuthenticatedClient, AuthenticatedClient]:
    return (
        AuthenticatedClient(
            client_id="operator",
            scopes=frozenset({"identity:admin", "identity:delegate"}),
        ),
        AuthenticatedClient(
            client_id=str(WORKER_ID),
            scopes=frozenset({"identity:delegate"}),
        ),
    )


async def _build_report() -> dict[str, Any]:
    store = InMemoryIdentityStore()
    provider = FixtureMailboxProvider()
    service = IdentityService(
        settings=IdentitySettings(
            agent_mail_domain="agents.aiat.local",
            outbound_relay_certified=True,
        ),
        store=store,
        stalwart=provider,
        resend=object(),
    )
    operator, worker_client = _clients()
    worker_actor = str(WORKER_ID)
    cases: list[dict[str, Any]] = []

    def passed(case: str, **detail: Any) -> None:
        cases.append({"case": case, "passed": True, **detail})

    def failed(case: str, exc: Exception) -> None:
        cases.append({"case": case, "passed": False, "error": f"{type(exc).__name__}: {exc}"})

    try:
        identity = await service.provision_identity(
            operator,
            company_id=COMPANY_ID,
            worker_id=WORKER_ID,
            actor_id="orchestrator-api",
            friendly_alias=None,
            idempotency_key=f"mailbox:{COMPANY_ID}:{WORKER_ID}",
        )
        provisioned_state = identity.get("state")
        await service.mailboxes.mark_delivery_verified(
            WORKER_ID, evidence={"provider_message_id": "fixture-verification"}
        )
        active = await store.get_identity(WORKER_ID)
        if (
            provisioned_state != IdentityState.IDENTITY_VERIFYING
            or active is None
            or active.get("state") != IdentityState.IDENTITY_ACTIVE
        ):
            raise AssertionError("worker identity did not reach the active state")
        passed("identity_setup", provider_calls=len(provider.calls))
    except Exception as exc:
        failed("identity_setup", exc)
        return {
            "schema_version": CHECK_SCHEMA,
            "status": "fail",
            "case_count": len(cases),
            "passed_case_count": 0,
            "cases": cases,
            "external_relay_calls": 0,
            "provider_submission_count": provider.submission_count,
            "network_access_performed": False,
            "mutation_performed": False,
            "secret_safe_report": True,
            "licence_metadata_is_gate": False,
            "errors": [case for case in cases if not case.get("passed")],
            "scope": "actual IdentityService outbound lifecycle with in-memory dependencies",
        }

    request_id: UUID | None = None
    approval_id: UUID | None = None
    try:
        first, approval = await service.request_outbound(
            worker_client,
            worker_id=WORKER_ID,
            actor_id=worker_actor,
            recipients=["recipient@example.net"],
            subject="fixture subject",
            body="fixture body must not enter the report",
            recipient_class="approved_external",
            idempotency_key="outbound-fixture-approved",
        )
        repeated, repeated_approval = await service.request_outbound(
            worker_client,
            worker_id=WORKER_ID,
            actor_id=worker_actor,
            recipients=["recipient@example.net"],
            subject="fixture subject",
            body="fixture body must not enter the report",
            recipient_class="approved_external",
            idempotency_key="outbound-fixture-approved",
        )
        request_id = first["id"]
        approval_id = approval["id"]
        if (
            request_id != repeated["id"]
            or approval_id != repeated_approval["id"]
            or approval["state"] != "PENDING"
        ):
            raise AssertionError("outbound request was not approval-paused and idempotent")
        denied = False
        try:
            await service.send_approved(
                worker_client,
                worker_id=WORKER_ID,
                actor_id=worker_actor,
                request_id=request_id,
                idempotency_key="submit-fixture-approved",
            )
        except PermissionError:
            denied = True
        if not denied or provider.submission_count != 0:
            raise AssertionError("unapproved outbound request reached the provider")
        passed("approval_pause_and_request_idempotency")
    except Exception as exc:
        failed("approval_pause_and_request_idempotency", exc)

    if request_id is not None and approval_id is not None:
        try:
            await service.decide_approval(
                operator,
                approval_id=approval_id,
                actor_id="operator",
                approved=True,
                reason="deterministic outbound fixture",
            )
            sent = await service.send_approved(
                worker_client,
                worker_id=WORKER_ID,
                actor_id=worker_actor,
                request_id=request_id,
                idempotency_key="submit-fixture-approved",
                trace_id="trace-outbound-fixture",
            )
            repeated = await service.send_approved(
                worker_client,
                worker_id=WORKER_ID,
                actor_id=worker_actor,
                request_id=request_id,
                idempotency_key="submit-fixture-approved",
            )
            attempt = store.delivery_attempts[-1]
            if (
                sent.get("state") != "SUBMITTED"
                or repeated.get("state") != "SUBMITTED"
                or provider.submission_count != 1
                or attempt.get("outcome") != "QUEUED"
                or attempt.get("trace_id") != "trace-outbound-fixture"
            ):
                raise AssertionError("approved outbound submission was not idempotent")
            passed("approved_submission_is_idempotent", provider_submissions=provider.submission_count)
        except Exception as exc:
            failed("approved_submission_is_idempotent", exc)

    try:
        failed_request, failed_approval = await service.request_outbound(
            worker_client,
            worker_id=WORKER_ID,
            actor_id=worker_actor,
            recipients=["retry@example.net"],
            subject="retry fixture",
            body="retry fixture body",
            recipient_class="approved_external",
            idempotency_key="outbound-fixture-retry",
        )
        await service.decide_approval(
            operator,
            approval_id=failed_approval["id"],
            actor_id="operator",
            approved=True,
            reason="deterministic outbound retry fixture",
        )
        provider.fail_mode = "definitive"
        with suppress(StalwartAdapterError):
            await service.send_approved(
                worker_client,
                worker_id=WORKER_ID,
                actor_id=worker_actor,
                request_id=failed_request["id"],
                idempotency_key="submit-fixture-retry",
            )
        failed_row = await store.get_outbound_request(failed_request["id"])
        failed_state = failed_row.get("state") if failed_row is not None else None
        provider.fail_mode = None
        retried = await service.send_approved(
            worker_client,
            worker_id=WORKER_ID,
            actor_id=worker_actor,
            request_id=failed_request["id"],
            idempotency_key="submit-fixture-retry",
        )
        if failed_state != "SUBMISSION_FAILED" or retried.get("state") != "SUBMITTED":
            raise AssertionError("definitive provider failure was not retryable")
        passed("definitive_provider_failure_is_retryable")
    except Exception as exc:
        failed("definitive_provider_failure_is_retryable", exc)

    try:
        unknown_request, unknown_approval = await service.request_outbound(
            worker_client,
            worker_id=WORKER_ID,
            actor_id=worker_actor,
            recipients=["unknown@example.net"],
            subject="unknown fixture",
            body="unknown fixture body",
            recipient_class="approved_external",
            idempotency_key="outbound-fixture-unknown",
        )
        await service.decide_approval(
            operator,
            approval_id=unknown_approval["id"],
            actor_id="operator",
            approved=True,
            reason="deterministic outbound outage fixture",
        )
        provider.fail_mode = "ambiguous"
        with suppress(StalwartAdapterError):
            await service.send_approved(
                worker_client,
                worker_id=WORKER_ID,
                actor_id=worker_actor,
                request_id=unknown_request["id"],
                idempotency_key="submit-fixture-unknown",
            )
        provider.fail_mode = None
        unknown_row = await store.get_outbound_request(unknown_request["id"])
        retry_denied = False
        try:
            await service.send_approved(
                worker_client,
                worker_id=WORKER_ID,
                actor_id=worker_actor,
                request_id=unknown_request["id"],
                idempotency_key="submit-fixture-unknown-retry",
            )
        except ValueError:
            retry_denied = True
        if unknown_row is None or unknown_row.get("state") != "SUBMISSION_UNKNOWN" or not retry_denied:
            raise AssertionError("ambiguous provider outage was not held for reconciliation")
        passed("ambiguous_provider_outage_requires_reconciliation")
    except Exception as exc:
        failed("ambiguous_provider_outage_requires_reconciliation", exc)

    rendered = json.dumps(cases, sort_keys=True, default=str)
    secret_safe = not any(
        marker in rendered
        for marker in (
            "fixture body must not enter the report",
            "retry fixture body",
            "unknown fixture body",
            "recipient@example.net",
            "retry@example.net",
            "unknown@example.net",
        )
    )
    if not secret_safe:
        failed("secret_safe_report", AssertionError("fixture report contains message content or recipient"))
    else:
        passed("secret_safe_report")

    errors = [case for case in cases if not case.get("passed")]
    return {
        "schema_version": CHECK_SCHEMA,
        "status": "pass" if not errors else "fail",
        "case_count": len(cases),
        "passed_case_count": len(cases) - len(errors),
        "cases": cases,
        "external_relay_calls": 0,
        "provider_submission_count": provider.submission_count,
        "mutation_scope": "in-memory identity store only",
        "network_access_performed": False,
        "mutation_performed": False,
        "secret_safe_report": secret_safe,
        "licence_metadata_is_gate": False,
        "errors": errors,
        "scope": "actual IdentityService outbound lifecycle with in-memory dependencies",
    }


def build_report() -> dict[str, Any]:
    return asyncio.run(_build_report())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="emit machine-readable output")
    parser.add_argument("--live", action="store_true", help="require live relay certification")
    args = parser.parse_args(argv)
    if args.live:
        report: dict[str, Any] = {
            "schema_version": CHECK_SCHEMA,
            "mode": "live",
            "status": "blocked",
            "reason": "live Resend/Stalwart outbound delivery certification requires operator-owned relay credentials and a safe recipient",
            "network_access_performed": False,
            "mutation_performed": False,
            "licence_metadata_is_gate": False,
        }
        exit_code = 2
    else:
        report = {"mode": "fixture", **build_report()}
        exit_code = 0 if report["status"] == "pass" else 1
    if args.json:
        print(json.dumps(report, sort_keys=True, indent=2))
    else:
        print(f"outbound mail lifecycle: {report['status']}")
    return exit_code


if __name__ == "__main__":
    sys.exit(main())

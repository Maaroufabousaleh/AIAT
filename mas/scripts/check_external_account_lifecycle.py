"""Exercise the real external-account lifecycle against in-memory state.

This fixture uses the actual identity service and ``InMemoryIdentityStore``
with a local mailbox-provider stub only to establish an active worker
identity.  It proves category approval, idempotent signup, one-use browser
leases, credential-rotation revocation, immediate suspension, closure
approval, and fail-closed unknown categories.  No external account, browser,
credential, mail transport, or provider HTTP call is created.  Licence and
restriction metadata is outside the lifecycle predicate.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any
from uuid import UUID

IDENTITY_ROOT = Path(__file__).resolve().parents[1] / "apps" / "identity-service"
if str(IDENTITY_ROOT) not in sys.path:
    sys.path.insert(0, str(IDENTITY_ROOT))

from identity_service.config import IdentitySettings  # noqa: E402
from identity_service.external_accounts.service import ExternalAccountPolicyError  # noqa: E402
from identity_service.models import ExternalAccountState, IdentityState  # noqa: E402
from identity_service.service import AuthenticatedClient, IdentityService  # noqa: E402
from identity_service.store import InMemoryIdentityStore  # noqa: E402

CHECK_SCHEMA = "aiat.external-account-lifecycle.v1"
COMPANY_ID = UUID("00000000-0000-4000-a000-000000000821")
WORKER_ID = UUID("00000000-0000-4000-a000-000000000822")


class FixtureMailboxProvider:
    """Local mailbox stub; it never represents an external account provider."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    async def find_mailbox(self, _address: str) -> None:
        self.calls.append("find_mailbox")
        return None

    async def create_mailbox(self, _address: str, *, quota_mb: int, idempotency_key: str) -> dict[str, str]:
        self.calls.append("create_mailbox")
        return {"provider_account_id": "fixture-mailbox-account", "correlation_id": "fixture-mailbox-create"}

    async def get_mailbox(self, provider_account_id: str) -> dict[str, Any]:
        self.calls.append("get_mailbox")
        return {"provider_account_id": provider_account_id, "correlation_id": "fixture-mailbox-get", "result": {"list": []}}

    async def add_alias(self, *_args: Any) -> dict[str, str]:
        self.calls.append("add_alias")
        return {"correlation_id": "fixture-alias"}

    async def disable_mailbox(self, *_args: Any) -> dict[str, str]:
        self.calls.append("disable_mailbox")
        return {"correlation_id": "fixture-disable"}

    async def archive_mailbox(self, *_args: Any) -> dict[str, str]:
        self.calls.append("archive_mailbox")
        return {"correlation_id": "fixture-archive"}


def _clients() -> tuple[AuthenticatedClient, AuthenticatedClient]:
    return (
        AuthenticatedClient(
            client_id="operator",
            scopes=frozenset({"identity:admin", "identity:delegate"}),
        ),
        AuthenticatedClient(
            client_id=str(WORKER_ID),
            scopes=frozenset({"identity:browser-broker"}),
        ),
    )


async def _build_report() -> dict[str, Any]:
    store = InMemoryIdentityStore()
    mailbox_provider = FixtureMailboxProvider()
    service = IdentityService(
        settings=IdentitySettings(
            agent_mail_domain="agents.aiat.local",
            outbound_relay_certified=True,
        ),
        store=store,
        stalwart=mailbox_provider,
        resend=object(),
    )
    operator, worker_client = _clients()
    worker_actor = str(WORKER_ID)
    cases: list[dict[str, Any]] = []

    def passed(case: str, **detail: Any) -> None:
        cases.append({"case": case, "passed": True, **detail})

    def failed(case: str, exc: Exception) -> None:
        cases.append({"case": case, "passed": False, "error": f"{type(exc).__name__}: {exc}"})

    identity: dict[str, Any] | None = None
    approved_account: dict[str, Any] | None = None
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
            WORKER_ID,
            evidence={"provider_message_id": "fixture-verification"},
        )
        active_identity = await store.get_identity(WORKER_ID)
        if provisioned_state != IdentityState.IDENTITY_VERIFYING or active_identity is None or active_identity.get("state") != IdentityState.IDENTITY_ACTIVE:
            raise AssertionError("worker identity did not reach the active state")
        if not await store.has_identity_access_grant(
            worker_id=WORKER_ID,
            identity_id=identity["id"],
            grant_type="mailbox",
        ):
            raise AssertionError("mailbox grant was not persisted")
        passed("identity_setup", mailbox_provider_calls=len(mailbox_provider.calls))
    except Exception as exc:
        failed("identity_setup", exc)

    account: dict[str, Any] | None = None
    if identity is not None:
        try:
            account = await service.signup_external_account(
                worker_client,
                worker_id=WORKER_ID,
                actor_id=worker_actor,
                service="github",
                service_category="github_organization",
                idempotency_key="external-signup-1",
                email_identity_id=identity["id"],
            )
            repeated = await service.signup_external_account(
                worker_client,
                worker_id=WORKER_ID,
                actor_id=worker_actor,
                service="github",
                service_category="github_organization",
                idempotency_key="external-signup-1",
                email_identity_id=identity["id"],
            )
            if account["state"] != ExternalAccountState.REQUESTED or account["id"] != repeated["id"]:
                raise AssertionError("category-sensitive signup was not pending/idempotent")
            approval = store.approvals[account["approval_id"]]
            if approval["state"] != "PENDING":
                raise AssertionError("organization signup did not create a pending approval")
            decision = await service.decide_approval(
                operator,
                approval_id=account["approval_id"],
                actor_id="operator",
                approved=True,
                reason="deterministic lifecycle fixture",
            )
            approved_account = await store.get_external_account(account["id"])
            if decision is None or approved_account is None or approved_account["state"] != ExternalAccountState.ACTIVE:
                raise AssertionError("approved signup did not activate the account")
            passed("category_approval_and_signup_idempotency", approval_decided=True)
        except Exception as exc:
            failed("category_approval_and_signup_idempotency", exc)

    session: dict[str, Any] | None = None
    lease: dict[str, Any] | None = None
    if approved_account is not None:
        try:
            session = await service.create_browser_session(
                worker_client,
                worker_id=WORKER_ID,
                actor_id=worker_actor,
                service="github",
                external_account_id=approved_account["id"],
                idempotency_key="browser-session-1",
            )
            lease = await service.issue_browser_session_lease(
                worker_client,
                worker_id=WORKER_ID,
                actor_id=worker_actor,
                session_id=session["id"],
            )
            used = await service.use_browser_session(
                worker_client,
                worker_id=WORKER_ID,
                actor_id=worker_actor,
                session_id=session["id"],
                lease_token=lease["lease_token"],
            )
            second_use_denied = False
            try:
                await service.use_browser_session(
                    worker_client,
                    worker_id=WORKER_ID,
                    actor_id=worker_actor,
                    session_id=session["id"],
                    lease_token=lease["lease_token"],
                )
            except PermissionError:
                second_use_denied = True
            if used["state"] != "ACTIVE" or not second_use_denied:
                raise AssertionError("browser lease was not short-lived and one-use")
            passed("browser_session_one_use_lease", second_use_denied=True)
        except Exception as exc:
            failed("browser_session_one_use_lease", exc)

    if approved_account is not None and session is not None:
        try:
            old_credential_ref = str(approved_account["credential_ref"])
            request = await service.request_external_credential_rotation(
                worker_client,
                worker_id=WORKER_ID,
                actor_id=worker_actor,
                account_id=approved_account["id"],
                idempotency_key="credential-rotation-1",
            )
            before = await store.get_external_account(approved_account["id"])
            if request["rotation"] != "PENDING_APPROVAL" or before is None or before["credential_ref"] != old_credential_ref:
                raise AssertionError("credential rotation bypassed its approval pause")
            await service.decide_approval(
                operator,
                approval_id=request["approval"]["id"],
                actor_id="operator",
                approved=True,
                reason="deterministic lifecycle fixture",
            )
            after = await store.get_external_account(approved_account["id"])
            session_after = await store.get_browser_session(session["id"])
            if after is None or after["credential_ref"] == old_credential_ref or session_after is None or session_after["state"] != "REVOKED":
                raise AssertionError("approved credential rotation did not rotate/revoke")
            passed("credential_rotation_revokes_sessions", session_state=session_after["state"])
        except Exception as exc:
            failed("credential_rotation_revokes_sessions", exc)

    close_session: dict[str, Any] | None = None
    if approved_account is not None:
        try:
            close_session = await service.create_browser_session(
                worker_client,
                worker_id=WORKER_ID,
                actor_id=worker_actor,
                service="github",
                external_account_id=approved_account["id"],
                idempotency_key="browser-session-close",
            )
            close_request = await service.request_external_account_close(
                worker_client,
                worker_id=WORKER_ID,
                actor_id=worker_actor,
                account_id=approved_account["id"],
                idempotency_key="external-close-1",
            )
            still_active = await store.get_external_account(approved_account["id"])
            if close_request["state"] != "PENDING_APPROVAL" or still_active is None or still_active["state"] != ExternalAccountState.ACTIVE:
                raise AssertionError("close request changed state before human approval")
            await service.decide_approval(
                operator,
                approval_id=close_request["approval"]["id"],
                actor_id="operator",
                approved=True,
                reason="deterministic lifecycle fixture",
            )
            closed = await store.get_external_account(approved_account["id"])
            closed_session = await store.get_browser_session(close_session["id"])
            if closed is None or closed["state"] != ExternalAccountState.CLOSED or closed_session is None or closed_session["state"] != "REVOKED":
                raise AssertionError("approved close did not close/revoke")
            passed("closure_requires_approval_and_revokes", session_state=closed_session["state"])
        except Exception as exc:
            failed("closure_requires_approval_and_revokes", exc)

    if identity is not None:
        try:
            denied = False
            try:
                await service.signup_external_account(
                    worker_client,
                    worker_id=WORKER_ID,
                    actor_id=worker_actor,
                    service="unknown-provider",
                    service_category="unlisted-provider",
                    idempotency_key="external-signup-unknown",
                    email_identity_id=identity["id"],
                )
            except ExternalAccountPolicyError:
                denied = True
            if not denied:
                raise AssertionError("unknown external-account category was not denied")
            passed("unknown_category_fails_closed")
        except Exception as exc:
            failed("unknown_category_fails_closed", exc)

    if identity is not None:
        try:
            dev_account = await service.signup_external_account(
                worker_client,
                worker_id=WORKER_ID,
                actor_id=worker_actor,
                service="local-test",
                service_category="development_test",
                idempotency_key="external-signup-dev",
                email_identity_id=identity["id"],
            )
            dev_session = await service.create_browser_session(
                worker_client,
                worker_id=WORKER_ID,
                actor_id=worker_actor,
                service="local-test",
                external_account_id=dev_account["id"],
                idempotency_key="browser-session-suspend",
            )
            suspended = await service.set_external_account_state(
                worker_client,
                worker_id=WORKER_ID,
                actor_id=worker_actor,
                account_id=dev_account["id"],
                state=ExternalAccountState.SUSPENDED,
            )
            suspended_session = await store.get_browser_session(dev_session["id"])
            closed_state_denied = False
            try:
                await service.set_external_account_state(
                    worker_client,
                    worker_id=WORKER_ID,
                    actor_id=worker_actor,
                    account_id=dev_account["id"],
                    state=ExternalAccountState.CLOSED,
                )
            except PermissionError:
                closed_state_denied = True
            if suspended["state"] != ExternalAccountState.SUSPENDED or suspended_session is None or suspended_session["state"] != "REVOKED" or not closed_state_denied:
                raise AssertionError("suspension did not revoke or direct close was allowed")
            passed("suspension_is_immediate_and_revokes", direct_close_denied=True)
        except Exception as exc:
            failed("suspension_is_immediate_and_revokes", exc)

    raw_markers = {"fixture-short-lived", "fixture-mailbox-account", "fixture-mailbox-create"}
    rendered = json.dumps(cases, sort_keys=True, default=str)
    secret_safe = not any(marker in rendered for marker in raw_markers) and all(
        key not in rendered for key in ("lease_token", "lease_hash")
    )
    if not secret_safe:
        failed("secret_safe_report", AssertionError("fixture report contains credential-shaped material"))
    else:
        passed("secret_safe_report")

    errors = [case for case in cases if not case.get("passed")]
    return {
        "schema_version": CHECK_SCHEMA,
        "status": "pass" if not errors else "fail",
        "case_count": len(cases),
        "passed_case_count": len(cases) - len(errors),
        "cases": cases,
        "external_provider_calls": 0,
        "mailbox_fixture_calls": len(mailbox_provider.calls),
        "mutation_scope": "in-memory identity store only",
        "network_access_performed": False,
        "mutation_performed": False,
        "secret_safe_report": secret_safe,
        "licence_metadata_is_gate": False,
        "errors": errors,
        "scope": "actual IdentityService external-account/browser lifecycle with in-memory dependencies",
    }


def build_report() -> dict[str, Any]:
    return asyncio.run(_build_report())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="emit machine-readable output")
    parser.add_argument("--live", action="store_true", help="require provider-specific live certification")
    args = parser.parse_args(argv)
    if args.live:
        report: dict[str, Any] = {
            "schema_version": CHECK_SCHEMA,
            "mode": "live",
            "status": "blocked",
            "reason": "external-account provider, browser, and outage/restore certification requires a selected sandbox",
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
        print(f"external-account lifecycle: {report['status']}")
    return exit_code


if __name__ == "__main__":
    sys.exit(main())

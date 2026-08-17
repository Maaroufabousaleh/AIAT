"""Identity-state persistence with an in-memory test backend and Postgres backend.

Only opaque credential references, hashes, and sanitized metadata cross this
boundary.  No table or method accepts a raw password, cookie, API key, refresh
token, TOTP seed, or recovery code.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any, Protocol
from uuid import UUID, uuid4

import sqlalchemy as sa
from cryptography.fernet import Fernet
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from mas_core.observability.mail_edge import MailEdgeObservation

from .models import ApprovalState, ExternalAccountState, IdentityState, UsageHoldState, redact
from .observability import new_span_id, normalize_span_id, normalize_trace_id


class IdentityStore(Protocol):
    async def close(self) -> None: ...
    async def healthcheck(self) -> bool: ...
    async def consume_client_nonce(self, client_id: str, nonce: str, expires_at: int) -> bool: ...
    async def ensure_client_registration(self, *, client_id: str, public_key: str, scopes: list[str]) -> dict[str, Any]: ...
    async def get_client_registration(self, client_id: str) -> dict[str, Any] | None: ...
    async def upsert_email_domain(self, *, domain: str, state: str, provider_domain_id: str | None, evidence: dict[str, Any], created_by: str) -> dict[str, Any]: ...
    async def provision_identity(
        self, *, company_id: UUID, worker_id: UUID, address: str, alias: str | None,
        domain: str, idempotency_key: str, quota_mb: int
    ) -> tuple[dict[str, Any], bool]: ...
    async def get_identity(self, worker_id: UUID) -> dict[str, Any] | None: ...
    async def set_identity_state(self, worker_id: UUID, state: IdentityState, evidence: dict[str, Any], *, outbox_event_type: str | None = None, outbox_payload: dict[str, Any] | None = None) -> dict[str, Any] | None: ...
    async def set_provider_account(self, worker_id: UUID, provider_account_id: str | None) -> dict[str, Any] | None: ...
    async def record_email_alias(self, *, identity_id: UUID, address: str) -> dict[str, Any]: ...
    async def start_provisioning_job(self, *, identity_id: UUID, company_id: UUID, worker_id: UUID, idempotency_key: str) -> dict[str, Any]: ...
    async def get_provisioning_job(self, idempotency_key: str) -> dict[str, Any] | None: ...
    async def finish_provisioning_job(self, *, idempotency_key: str, state: str, provider_correlation_id: str | None, evidence: dict[str, Any]) -> dict[str, Any] | None: ...
    async def record_mail_event(self, *, identity_id: UUID, provider_message_id: str | None, event_type: str, metadata: dict[str, Any]) -> dict[str, Any]: ...
    async def record_mail_edge_observation(self, observation: MailEdgeObservation) -> dict[str, Any]: ...
    async def record_verification_transaction(self, *, identity_id: UUID, provider_message_id: str, idempotency_key: str, code_hash: str | None, link_hash: str | None, state: str) -> dict[str, Any]: ...
    async def create_identity_access_grant(self, *, worker_id: UUID, identity_id: UUID, grant_type: str, issued_by: str) -> dict[str, Any]: ...
    async def has_identity_access_grant(self, *, worker_id: UUID, identity_id: UUID, grant_type: str) -> bool: ...
    async def create_outbox(self, event_type: str, aggregate_type: str, aggregate_id: str, payload: dict[str, Any]) -> dict[str, Any]: ...
    async def list_outbox(self, cursor: int, limit: int) -> list[dict[str, Any]]: ...
    async def get_max_outbox_sequence(self) -> int: ...
    async def get_client_cursor(self, client_id: str) -> int: ...
    async def advance_client_cursor(self, client_id: str, cursor: int) -> int: ...
    async def consume_provider_rate(self, *, provider: str, rate_key: str, window_started_at: datetime, limit: int) -> bool: ...
    async def create_audit(self, **kwargs: Any) -> dict[str, Any]: ...
    async def create_approval(self, *, worker_id: UUID, kind: str, target_id: UUID, idempotency_key: str) -> dict[str, Any]: ...
    async def decide_approval(self, approval_id: UUID, state: ApprovalState, actor_id: str, reason: str) -> dict[str, Any] | None: ...
    async def get_approval_for_target(self, target_id: UUID) -> dict[str, Any] | None: ...
    async def create_outbound_request(self, **kwargs: Any) -> tuple[dict[str, Any], bool]: ...
    async def get_outbound_request(self, request_id: UUID) -> dict[str, Any] | None: ...
    async def get_outbound_request_metadata(self, request_id: UUID) -> dict[str, Any] | None: ...
    async def find_outbound_request_by_provider_message_id(self, provider_message_id: str) -> dict[str, Any] | None: ...
    async def claim_outbound_submission(self, request_id: UUID) -> tuple[dict[str, Any] | None, bool]: ...
    async def update_outbound_request(self, request_id: UUID, **values: Any) -> dict[str, Any] | None: ...
    async def record_delivery_attempt(self, *, outbound_request_id: UUID, provider_correlation_id: str | None, provider_message_id: str | None, outcome: str, failure_class: str | None = None, sanitized_reason: str | None = None, trace_id: str | None = None, span_id: str | None = None) -> dict[str, Any]: ...
    async def create_external_account(self, **kwargs: Any) -> tuple[dict[str, Any], bool]: ...
    async def get_external_account(self, account_id: UUID) -> dict[str, Any] | None: ...
    async def bind_external_account(self, account_id: UUID, *, approval_id: UUID, credential_ref: str) -> dict[str, Any] | None: ...
    async def update_external_account(self, account_id: UUID, state: ExternalAccountState) -> dict[str, Any] | None: ...
    async def suspend_external_accounts(self, worker_id: UUID) -> int: ...
    async def create_credential_lease(self, *, external_account_id: UUID, worker_id: UUID, lease_hash: str, scope: str, expires_at: datetime) -> dict[str, Any]: ...
    async def consume_credential_lease(self, *, external_account_id: UUID, worker_id: UUID, lease_hash: str, scope: str) -> bool: ...
    async def create_browser_session(self, **kwargs: Any) -> tuple[dict[str, Any], bool]: ...
    async def get_browser_session(self, session_id: UUID) -> dict[str, Any] | None: ...
    async def revoke_browser_sessions(self, worker_id: UUID) -> int: ...
    async def reserve_hold(self, *, worker_id: UUID, kind: str, idempotency_key: str, units: int) -> tuple[dict[str, Any], bool]: ...
    async def settle_hold(self, hold_id: UUID, state: UsageHoldState) -> dict[str, Any] | None: ...
    async def dashboard_rows(self, resource: str, limit: int = 100) -> list[dict[str, Any]]: ...


def _now() -> datetime:
    return datetime.now(UTC)


def _mail_edge_row(observation: MailEdgeObservation) -> dict[str, Any]:
    """Convert the shared observation into a persistence-safe scalar row."""

    row = observation.model_dump(mode="python")
    row["received_at"] = _now()
    return row


def _mail_edge_conflicts(row: dict[str, Any], observation: MailEdgeObservation) -> bool:
    """Reject a reused provider event ID whose normalized meaning changed."""

    expected = observation.model_dump(mode="python")
    for field in (
        "provider", "source", "event_id", "event_type", "outcome", "failure_class",
        "worker_id", "outbound_request_id", "provider_message_ref", "trace_id", "span_id",
        "signature_verified", "metadata", "occurred_at",
    ):
        actual = (
            row.get("metadata_json", row.get("metadata"))
            if field == "metadata"
            else row.get(field)
        )
        wanted = expected.get(field)
        if field == "occurred_at":
            actual = actual.astimezone(UTC) if isinstance(actual, datetime) and actual.tzinfo else actual
            wanted = wanted.astimezone(UTC) if isinstance(wanted, datetime) and wanted.tzinfo else wanted
        if actual != wanted:
            return True
    return False


class InMemoryIdentityStore:
    """Deterministic backend used by policy/adapter tests; never a production fallback."""

    def __init__(self) -> None:
        self.identities_by_worker: dict[UUID, dict[str, Any]] = {}
        self.domains: dict[str, dict[str, Any]] = {}
        self.identities_by_key: dict[str, UUID] = {}
        self.email_aliases: dict[str, dict[str, Any]] = {}
        self.identity_grants: dict[tuple[UUID, UUID, str], dict[str, Any]] = {}
        self.provisioning_jobs: dict[str, dict[str, Any]] = {}
        self.mail_events: dict[tuple[UUID, str | None, str], dict[str, Any]] = {}
        self.mail_edge_observations: dict[tuple[str, str], dict[str, Any]] = {}
        self.verification_transactions: dict[str, dict[str, Any]] = {}
        self.nonces: dict[tuple[str, str], int] = {}
        self.client_registrations: dict[str, dict[str, Any]] = {}
        self.client_cursors: dict[str, int] = {}
        self.provider_rates: dict[tuple[str, str, datetime], int] = {}
        self.outbox: list[dict[str, Any]] = []
        self.audit: list[dict[str, Any]] = []
        self.approvals: dict[UUID, dict[str, Any]] = {}
        self.outbound: dict[UUID, dict[str, Any]] = {}
        self.outbound_keys: dict[str, UUID] = {}
        self.delivery_attempts: list[dict[str, Any]] = []
        self.external_accounts: dict[UUID, dict[str, Any]] = {}
        self.external_keys: dict[str, UUID] = {}
        self.sessions: dict[UUID, dict[str, Any]] = {}
        self.session_keys: dict[str, UUID] = {}
        self.credential_leases: dict[str, dict[str, Any]] = {}
        self.holds: dict[UUID, dict[str, Any]] = {}
        self.hold_keys: dict[str, UUID] = {}
        self.state_transitions: list[dict[str, Any]] = []
        self.usage_events: list[dict[str, Any]] = []

    async def close(self) -> None:
        return None

    async def healthcheck(self) -> bool:
        return True

    async def consume_client_nonce(self, client_id: str, nonce: str, expires_at: int) -> bool:
        now = int(datetime.now(UTC).timestamp())
        self.nonces = {key: expiry for key, expiry in self.nonces.items() if expiry > now}
        key = (client_id, nonce)
        if key in self.nonces:
            return False
        self.nonces[key] = expires_at
        return True

    async def ensure_client_registration(self, *, client_id: str, public_key: str, scopes: list[str]) -> dict[str, Any]:
        row = self.client_registrations.get(client_id)
        if row is None:
            row = {
                "id": uuid4(), "client_id": client_id, "public_key": public_key,
                "scopes": sorted(set(scopes)), "state": "ACTIVE",
                "created_at": _now(), "updated_at": _now(),
            }
            self.client_registrations[client_id] = row
        return row

    async def get_client_registration(self, client_id: str) -> dict[str, Any] | None:
        return self.client_registrations.get(client_id)

    async def upsert_email_domain(self, *, domain: str, state: str, provider_domain_id: str | None, evidence: dict[str, Any], created_by: str) -> dict[str, Any]:
        now = _now()
        row = self.domains.get(domain)
        if row is None:
            row = {"id": uuid4(), "domain": domain, "created_at": now, "created_by": created_by}
            self.domains[domain] = row
        row.update({"state": state, "provider_domain_id": provider_domain_id or row.get("provider_domain_id"), "verification_evidence": redact(evidence), "updated_at": now})
        return row

    async def provision_identity(self, *, company_id: UUID, worker_id: UUID, address: str, alias: str | None, domain: str, idempotency_key: str, quota_mb: int) -> tuple[dict[str, Any], bool]:
        existing_id = self.identities_by_key.get(idempotency_key)
        if existing_id is not None:
            return self.identities_by_worker[self.identities_by_key[idempotency_key]], False
        if worker_id in self.identities_by_worker:
            return self.identities_by_worker[worker_id], False
        now = _now()
        domain_row = self.domains.get(domain)
        if domain_row is None:
            domain_row = {
                "id": uuid4(), "domain": domain, "state": "PENDING_VERIFICATION",
                "provider_domain_id": None, "verification_evidence": {},
                "created_by": "identity-service", "created_at": now,
                "updated_at": now,
            }
            self.domains[domain] = domain_row
        row = {
            "id": uuid4(), "company_id": company_id, "worker_id": worker_id,
            "domain_id": domain_row["id"],
            "address": address, "alias": alias, "state": IdentityState.HIRED_PENDING_IDENTITY,
            "quota_mb": quota_mb, "outbound_enabled": False, "provider_account_id": None,
            "idempotency_key": idempotency_key, "created_at": now, "updated_at": now,
        }
        self.identities_by_worker[worker_id] = row
        self.identities_by_key[idempotency_key] = worker_id
        return row, True

    async def get_identity(self, worker_id: UUID) -> dict[str, Any] | None:
        return self.identities_by_worker.get(worker_id)

    async def set_identity_state(self, worker_id: UUID, state: IdentityState, evidence: dict[str, Any], *, outbox_event_type: str | None = None, outbox_payload: dict[str, Any] | None = None) -> dict[str, Any] | None:
        row = self.identities_by_worker.get(worker_id)
        if row is None:
            return None
        previous_state = row.get("state")
        row["state"] = state
        row["updated_at"] = _now()
        row["state_evidence"] = redact(evidence)
        self.state_transitions.append({
            "id": uuid4(), "identity_id": row["id"],
            "from_state": previous_state, "to_state": state,
            "evidence": redact(evidence), "occurred_at": _now(),
        })
        if outbox_event_type:
            await self.create_outbox(
                outbox_event_type,
                "agent_email_identity",
                str(row["id"]),
                outbox_payload or {},
            )
        return row

    async def set_provider_account(self, worker_id: UUID, provider_account_id: str | None) -> dict[str, Any] | None:
        row = self.identities_by_worker.get(worker_id)
        if row is None:
            return None
        row["provider_account_id"] = provider_account_id
        row["updated_at"] = _now()
        return row

    async def record_email_alias(self, *, identity_id: UUID, address: str) -> dict[str, Any]:
        row = self.email_aliases.get(address)
        if row is None:
            row = {
                "id": uuid4(), "identity_id": identity_id, "address": address,
                "state": "ACTIVE", "created_at": _now(),
            }
            self.email_aliases[address] = row
        elif row["identity_id"] != identity_id:
            raise ValueError("email alias is already owned by another identity")
        row.update({"state": "ACTIVE", "updated_at": _now()})
        return row

    async def start_provisioning_job(self, *, identity_id: UUID, company_id: UUID, worker_id: UUID, idempotency_key: str) -> dict[str, Any]:
        row = self.provisioning_jobs.get(idempotency_key)
        if row is None:
            row = {
                "id": uuid4(), "identity_id": identity_id, "company_id": company_id,
                "worker_id": worker_id, "idempotency_key": idempotency_key,
                "attempt_count": 0, "created_at": _now(),
            }
            self.provisioning_jobs[idempotency_key] = row
        row.update({"state": "RUNNING", "attempt_count": int(row["attempt_count"]) + 1, "updated_at": _now()})
        return row

    async def get_provisioning_job(
        self, idempotency_key: str
    ) -> dict[str, Any] | None:
        return self.provisioning_jobs.get(idempotency_key)

    async def finish_provisioning_job(self, *, idempotency_key: str, state: str, provider_correlation_id: str | None, evidence: dict[str, Any]) -> dict[str, Any] | None:
        row = self.provisioning_jobs.get(idempotency_key)
        if row is None:
            return None
        row.update({"state": state, "provider_correlation_id": provider_correlation_id, "evidence": redact(evidence), "updated_at": _now()})
        return row

    async def record_mail_event(self, *, identity_id: UUID, provider_message_id: str | None, event_type: str, metadata: dict[str, Any]) -> dict[str, Any]:
        key = (identity_id, provider_message_id, event_type)
        row = self.mail_events.get(key)
        if row is None:
            row = {"id": uuid4(), "identity_id": identity_id, "provider_message_id": provider_message_id, "event_type": event_type, "occurred_at": _now()}
            self.mail_events[key] = row
        row["metadata"] = redact(metadata)
        return row

    async def record_mail_edge_observation(self, observation: MailEdgeObservation) -> dict[str, Any]:
        key = (observation.provider, observation.event_id)
        existing = self.mail_edge_observations.get(key)
        if existing is not None:
            if _mail_edge_conflicts(existing, observation):
                raise ValueError("mail-edge provider event ID was reused with different data")
            return existing
        row = _mail_edge_row(observation)
        self.mail_edge_observations[key] = row
        return row

    async def record_verification_transaction(self, *, identity_id: UUID, provider_message_id: str, idempotency_key: str, code_hash: str | None, link_hash: str | None, state: str) -> dict[str, Any]:
        row = self.verification_transactions.get(idempotency_key)
        if row is None:
            row = {"id": uuid4(), "identity_id": identity_id, "provider_message_id": provider_message_id, "idempotency_key": idempotency_key, "created_at": _now()}
            self.verification_transactions[idempotency_key] = row
        if code_hash is not None:
            row["code_hash"] = code_hash
        if link_hash is not None:
            row["link_hash"] = link_hash
        row.update({"state": state, "updated_at": _now()})
        return row

    async def create_identity_access_grant(self, *, worker_id: UUID, identity_id: UUID, grant_type: str, issued_by: str) -> dict[str, Any]:
        key = (worker_id, identity_id, grant_type)
        existing = self.identity_grants.get(key)
        if existing is not None:
            return existing
        row = {
            "id": uuid4(), "worker_id": worker_id, "identity_id": identity_id,
            "grant_type": grant_type, "state": "ACTIVE", "issued_by": issued_by,
            "created_at": _now(), "updated_at": _now(),
        }
        self.identity_grants[key] = row
        return row

    async def has_identity_access_grant(self, *, worker_id: UUID, identity_id: UUID, grant_type: str) -> bool:
        row = self.identity_grants.get((worker_id, identity_id, grant_type))
        return bool(row and row.get("state") == "ACTIVE")

    async def create_outbox(self, event_type: str, aggregate_type: str, aggregate_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        event = {"sequence": len(self.outbox) + 1, "id": uuid4(), "event_type": event_type, "aggregate_type": aggregate_type, "aggregate_id": aggregate_id, "payload": redact(payload), "occurred_at": _now()}
        self.outbox.append(event)
        return event

    async def list_outbox(self, cursor: int, limit: int) -> list[dict[str, Any]]:
        return [event for event in self.outbox if event["sequence"] > cursor][:limit]

    async def get_max_outbox_sequence(self) -> int:
        return int(self.outbox[-1]["sequence"]) if self.outbox else 0

    async def get_client_cursor(self, client_id: str) -> int:
        return self.client_cursors.get(client_id, 0)

    async def advance_client_cursor(self, client_id: str, cursor: int) -> int:
        self.client_cursors[client_id] = max(self.client_cursors.get(client_id, 0), cursor)
        return self.client_cursors[client_id]

    async def consume_provider_rate(self, *, provider: str, rate_key: str, window_started_at: datetime, limit: int) -> bool:
        key = (provider, rate_key, window_started_at)
        count = self.provider_rates.get(key, 0)
        if count >= limit:
            return False
        self.provider_rates[key] = count + 1
        return True

    async def create_audit(self, **kwargs: Any) -> dict[str, Any]:
        row = {"id": uuid4(), "occurred_at": _now(), **redact(kwargs)}
        self.audit.append(row)
        return row

    async def create_approval(self, *, worker_id: UUID, kind: str, target_id: UUID, idempotency_key: str) -> dict[str, Any]:
        for approval in self.approvals.values():
            if approval["idempotency_key"] == idempotency_key:
                return approval
        row = {"id": uuid4(), "worker_id": worker_id, "kind": kind, "target_id": target_id, "idempotency_key": idempotency_key, "state": ApprovalState.PENDING, "created_at": _now(), "decided_by": None, "reason": ""}
        self.approvals[row["id"]] = row
        return row

    async def decide_approval(self, approval_id: UUID, state: ApprovalState, actor_id: str, reason: str) -> dict[str, Any] | None:
        row = self.approvals.get(approval_id)
        if row is None or row["state"] != ApprovalState.PENDING:
            return None
        row.update({"state": state, "decided_by": actor_id, "reason": reason, "decided_at": _now()})
        return row

    async def get_approval_for_target(self, target_id: UUID) -> dict[str, Any] | None:
        values = [row for row in self.approvals.values() if row["target_id"] == target_id]
        return values[-1] if values else None

    async def create_outbound_request(self, **kwargs: Any) -> tuple[dict[str, Any], bool]:
        key = str(kwargs["idempotency_key"])
        if key in self.outbound_keys:
            return self.outbound[self.outbound_keys[key]], False
        row = {"id": uuid4(), "state": "PENDING_APPROVAL", "provider_message_id": None, "created_at": _now(), "updated_at": _now(), **redact(kwargs)}
        self.outbound[row["id"]] = row
        self.outbound_keys[key] = row["id"]
        return row, True

    async def get_outbound_request(self, request_id: UUID) -> dict[str, Any] | None:
        return self.outbound.get(request_id)

    async def get_outbound_request_metadata(self, request_id: UUID) -> dict[str, Any] | None:
        row = self.outbound.get(request_id)
        if row is None:
            return None
        return {
            key: row.get(key)
            for key in (
                "id", "identity_id", "worker_id", "provider_message_id",
                "provider_correlation_id", "state",
            )
        }

    async def find_outbound_request_by_provider_message_id(self, provider_message_id: str) -> dict[str, Any] | None:
        for row in self.outbound.values():
            if str(row.get("provider_message_id") or "") == provider_message_id:
                return await self.get_outbound_request_metadata(row["id"])
        return None

    async def claim_outbound_submission(self, request_id: UUID) -> tuple[dict[str, Any] | None, bool]:
        row = self.outbound.get(request_id)
        if row is None or str(row.get("state")) not in {"PENDING_APPROVAL", "SUBMISSION_FAILED"}:
            return row, False
        row["state"] = "SUBMITTING"
        row["updated_at"] = _now()
        return row, True

    async def update_outbound_request(self, request_id: UUID, **values: Any) -> dict[str, Any] | None:
        row = self.outbound.get(request_id)
        if row is None:
            return None
        row.update(redact(values))
        row["updated_at"] = _now()
        return row

    async def record_delivery_attempt(self, *, outbound_request_id: UUID, provider_correlation_id: str | None, provider_message_id: str | None, outcome: str, failure_class: str | None = None, sanitized_reason: str | None = None, trace_id: str | None = None, span_id: str | None = None) -> dict[str, Any]:
        row = {
            "id": uuid4(), "outbound_request_id": outbound_request_id,
            "attempt_number": sum(1 for item in self.delivery_attempts if item["outbound_request_id"] == outbound_request_id) + 1,
            "provider_correlation_id": provider_correlation_id,
            "provider_message_id": provider_message_id,
            "outcome": outcome, "failure_class": failure_class,
            "sanitized_reason": sanitized_reason,
            "trace_id": normalize_trace_id(trace_id),
            "span_id": normalize_span_id(span_id) or (new_span_id() if normalize_trace_id(trace_id) else None),
            "attempted_at": _now(),
        }
        self.delivery_attempts.append(redact(row))
        return row

    async def create_external_account(self, **kwargs: Any) -> tuple[dict[str, Any], bool]:
        key = str(kwargs["idempotency_key"])
        if key in self.external_keys:
            return self.external_accounts[self.external_keys[key]], False
        values = dict(kwargs)
        account_id = values.pop("account_id")
        row = {
            "id": account_id,
            "state": ExternalAccountState.REQUESTED,
            "created_at": _now(),
            "updated_at": _now(),
            **values,
        }
        self.external_accounts[row["id"]] = row
        self.external_keys[key] = row["id"]
        return row, True

    async def get_external_account(self, account_id: UUID) -> dict[str, Any] | None:
        return self.external_accounts.get(account_id)

    async def bind_external_account(self, account_id: UUID, *, approval_id: UUID, credential_ref: str) -> dict[str, Any] | None:
        row = self.external_accounts.get(account_id)
        if row is None:
            return None
        row.update({
            "approval_id": approval_id,
            "credential_ref": credential_ref,
            "updated_at": _now(),
        })
        return row

    async def update_external_account(self, account_id: UUID, state: ExternalAccountState) -> dict[str, Any] | None:
        row = self.external_accounts.get(account_id)
        if row is None:
            return None
        row["state"] = state
        row["updated_at"] = _now()
        return row

    async def suspend_external_accounts(self, worker_id: UUID) -> int:
        count = 0
        for row in self.external_accounts.values():
            if row["worker_id"] == worker_id and row["state"] != ExternalAccountState.CLOSED:
                row["state"] = ExternalAccountState.SUSPENDED
                row["updated_at"] = _now()
                count += 1
        return count

    async def create_credential_lease(self, *, external_account_id: UUID, worker_id: UUID, lease_hash: str, scope: str, expires_at: datetime) -> dict[str, Any]:
        existing = self.credential_leases.get(lease_hash)
        if existing is not None:
            return existing
        row = {
            "id": uuid4(), "external_account_id": external_account_id,
            "worker_id": worker_id, "lease_hash": lease_hash, "scope": scope,
            "state": "ACTIVE", "expires_at": expires_at, "used_at": None,
            "created_at": _now(),
        }
        self.credential_leases[lease_hash] = row
        return row

    async def consume_credential_lease(self, *, external_account_id: UUID, worker_id: UUID, lease_hash: str, scope: str) -> bool:
        row = self.credential_leases.get(lease_hash)
        if (
            row is None
            or row["external_account_id"] != external_account_id
            or row["worker_id"] != worker_id
            or row["scope"] != scope
            or row["state"] != "ACTIVE"
            or row["expires_at"] <= _now()
            or row.get("used_at") is not None
        ):
            return False
        row["state"] = "CONSUMED"
        row["used_at"] = _now()
        return True

    async def create_browser_session(self, **kwargs: Any) -> tuple[dict[str, Any], bool]:
        key = str(kwargs["idempotency_key"])
        if key in self.session_keys:
            return self.sessions[self.session_keys[key]], False
        row = {"id": uuid4(), "state": "ACTIVE", "created_at": _now(), "updated_at": _now(), "lease_version": 1, **redact(kwargs)}
        self.sessions[row["id"]] = row
        self.session_keys[key] = row["id"]
        return row, True

    async def get_browser_session(self, session_id: UUID) -> dict[str, Any] | None:
        return self.sessions.get(session_id)

    async def revoke_browser_sessions(self, worker_id: UUID) -> int:
        count = 0
        for row in self.sessions.values():
            if row["worker_id"] == worker_id and row["state"] == "ACTIVE":
                row["state"] = "REVOKED"
                row["lease_version"] += 1
                row["updated_at"] = _now()
                count += 1
        return count

    async def reserve_hold(self, *, worker_id: UUID, kind: str, idempotency_key: str, units: int) -> tuple[dict[str, Any], bool]:
        if idempotency_key in self.hold_keys:
            row = self.holds[self.hold_keys[idempotency_key]]
            if row["worker_id"] != worker_id or row["kind"] != kind or row["units"] != units:
                raise ValueError("identity budget hold idempotency key was reused with different inputs")
            if row["state"] == UsageHoldState.RELEASED:
                row["state"] = UsageHoldState.RESERVED
                row["updated_at"] = _now()
                return row, True
            return row, False
        row = {"id": uuid4(), "worker_id": worker_id, "kind": kind, "idempotency_key": idempotency_key, "units": units, "state": UsageHoldState.RESERVED, "created_at": _now(), "updated_at": _now()}
        self.holds[row["id"]] = row
        self.hold_keys[idempotency_key] = row["id"]
        return row, True

    async def settle_hold(self, hold_id: UUID, state: UsageHoldState) -> dict[str, Any] | None:
        row = self.holds.get(hold_id)
        if row is None or row["state"] != UsageHoldState.RESERVED:
            return None
        row["state"] = state
        row["updated_at"] = _now()
        if state == UsageHoldState.COMMITTED:
            self.usage_events.append({
                "id": uuid4(), "worker_id": row["worker_id"],
                "kind": row["kind"], "units": row["units"],
                "hold_id": hold_id,
                "idempotency_key": f"commit:{row['idempotency_key']}",
                "occurred_at": _now(),
            })
        return row

    async def dashboard_rows(self, resource: str, limit: int = 100) -> list[dict[str, Any]]:
        if resource == "mailboxes":
            rows: list[dict[str, Any]] = []
            for identity in self.identities_by_worker.values():
                row = dict(identity)
                row["aliases"] = [
                    alias["address"] for alias in self.email_aliases.values()
                    if alias["identity_id"] == identity["id"] and alias.get("state") == "ACTIVE"
                ]
                rows.append(row)
            return rows[-limit:]
        if resource == "outbound-mail":
            rows = []
            for outbound in self.outbound.values():
                row = dict(outbound)
                approval = await self.get_approval_for_target(outbound["id"])
                row["approval_state"] = approval.get("state") if approval else None
                rows.append(row)
            return rows[-limit:]
        edge_rows = [
            {
                "id": row["id"],
                "outbound_request_id": row.get("outbound_request_id"),
                "provider_message_id": row.get("provider_message_ref"),
                "provider_correlation_id": None,
                "outcome": row.get("outcome"),
                "failure_class": row.get("failure_class"),
                "sanitized_reason": None,
                "trace_id": row.get("trace_id"),
                "span_id": row.get("span_id"),
                "attempted_at": row.get("occurred_at"),
                "occurred_at": row.get("occurred_at"),
                "source": row.get("source"),
                "event_type": row.get("event_type"),
                "signature_verified": row.get("signature_verified"),
                "provider_account_id": None,
            }
            for row in self.mail_edge_observations.values()
        ]
        if resource == "mail-edge":
            return edge_rows[-limit:]
        lookup = {
            "mail-domains": self.domains.values(),
            "identities": self.identities_by_worker.values(),
            "external-accounts": self.external_accounts.values(),
            "mail-relay": [*self.delivery_attempts, *edge_rows],
            "auth-sessions": self.sessions.values(), "identity-approvals": self.approvals.values(), "identity-audit": self.audit,
        }
        return list(lookup.get(resource, []))[-limit:]


class PostgresIdentityStore(InMemoryIdentityStore):
    """Postgres-backed operational backend.

    The SQL is intentionally narrow and explicit: all authoritative schema
    changes live in this app's Alembic migration.  It inherits no laptop MAS
    connection and cannot be constructed without the dedicated DSN.
    """

    def __init__(self, dsn: str, *, content_encryption_key: str = "") -> None:
        super().__init__()
        self.engine: AsyncEngine = create_async_engine(dsn, pool_pre_ping=True)
        self._content_fernet = Fernet(content_encryption_key.encode()) if content_encryption_key else None

    async def close(self) -> None:
        await self.engine.dispose()

    async def healthcheck(self) -> bool:
        try:
            async with self.engine.connect() as conn:
                return bool(await conn.scalar(sa.text("SELECT 1")))
        except Exception:
            return False

    async def _fetchone(self, statement: str, params: dict[str, Any]) -> dict[str, Any] | None:
        async with self.engine.begin() as conn:
            result = await conn.execute(sa.text(statement), params)
            row = result.mappings().first()
            return dict(row) if row else None

    async def consume_client_nonce(self, client_id: str, nonce: str, expires_at: int) -> bool:
        async with self.engine.begin() as conn:
            await conn.execute(sa.text("DELETE FROM identity_client_nonces WHERE expires_at <= now()"))
            result = await conn.execute(
                sa.text("""INSERT INTO identity_client_nonces (client_id, nonce, expires_at)
                           VALUES (:client_id, :nonce, to_timestamp(:expires_at))
                           ON CONFLICT (client_id, nonce) DO NOTHING RETURNING nonce"""),
                {"client_id": client_id, "nonce": nonce, "expires_at": expires_at},
            )
            return result.mappings().first() is not None

    async def ensure_client_registration(self, *, client_id: str, public_key: str, scopes: list[str]) -> dict[str, Any]:
        row = await self._fetchone(
            """INSERT INTO identity_client_registrations (id, client_id, public_key, scopes, state)
               VALUES (:id, :client_id, :public_key, CAST(:scopes AS jsonb), 'ACTIVE')
               ON CONFLICT (client_id) DO NOTHING
               RETURNING *""",
            {"id": uuid4(), "client_id": client_id, "public_key": public_key,
             "scopes": json.dumps(sorted(set(scopes)))},
        )
        if row is not None:
            return row
        existing = await self.get_client_registration(client_id)
        if existing is None:
            raise RuntimeError("identity client registration failed")
        return existing

    async def get_client_registration(self, client_id: str) -> dict[str, Any] | None:
        return await self._fetchone(
            "SELECT * FROM identity_client_registrations WHERE client_id = :client_id",
            {"client_id": client_id},
        )

    async def upsert_email_domain(self, *, domain: str, state: str, provider_domain_id: str | None, evidence: dict[str, Any], created_by: str) -> dict[str, Any]:
        row = await self._fetchone(
            """INSERT INTO email_domains (id, domain, state, provider_domain_id, verification_evidence, created_by)
               VALUES (:id, :domain, :state, :provider_domain_id, CAST(:evidence AS jsonb), :created_by)
               ON CONFLICT (domain) DO UPDATE SET
                 state = EXCLUDED.state,
                 provider_domain_id = COALESCE(EXCLUDED.provider_domain_id, email_domains.provider_domain_id),
                 verification_evidence = EXCLUDED.verification_evidence,
                 updated_at = now()
               RETURNING *""",
            {"id": uuid4(), "domain": domain, "state": state, "provider_domain_id": provider_domain_id, "evidence": json.dumps(redact(evidence)), "created_by": created_by},
        )
        assert row is not None
        return row

    async def provision_identity(self, *, company_id: UUID, worker_id: UUID, address: str, alias: str | None, domain: str, idempotency_key: str, quota_mb: int) -> tuple[dict[str, Any], bool]:
        row = await self._fetchone(
            """WITH selected_domain AS (
                 INSERT INTO email_domains
                   (id, domain, state, provider_domain_id, verification_evidence, created_by)
                 VALUES (:domain_id, :domain, 'PENDING_VERIFICATION', NULL, '{}'::jsonb, 'identity-service')
                 ON CONFLICT (domain) DO UPDATE SET domain = EXCLUDED.domain
                 RETURNING id
               )
               INSERT INTO agent_email_identities
                 (id, company_id, worker_id, domain_id, address, friendly_alias, state, quota_mb, idempotency_key)
               SELECT :id, :company_id, :worker_id, selected_domain.id, :address, :alias,
                      'HIRED_PENDING_IDENTITY', :quota_mb, :idempotency_key
               FROM selected_domain
               ON CONFLICT (idempotency_key) DO NOTHING
               RETURNING *""",
            {"id": uuid4(), "domain_id": uuid4(), "domain": domain,
             "company_id": company_id, "worker_id": worker_id,
             "address": address, "alias": alias, "quota_mb": quota_mb,
             "idempotency_key": idempotency_key},
        )
        if row is not None:
            return row, True
        existing = await self._fetchone(
            "SELECT * FROM agent_email_identities WHERE idempotency_key = :idempotency_key OR worker_id = :worker_id",
            {"idempotency_key": idempotency_key, "worker_id": worker_id},
        )
        if existing is None:
            raise RuntimeError("identity provisioning idempotency lookup failed")
        return existing, False

    async def get_identity(self, worker_id: UUID) -> dict[str, Any] | None:
        return await self._fetchone("SELECT * FROM agent_email_identities WHERE worker_id = :worker_id", {"worker_id": worker_id})

    async def set_identity_state(self, worker_id: UUID, state: IdentityState, evidence: dict[str, Any], *, outbox_event_type: str | None = None, outbox_payload: dict[str, Any] | None = None) -> dict[str, Any] | None:
        async with self.engine.begin() as conn:
            previous = await conn.execute(sa.text("SELECT id, state FROM agent_email_identities WHERE worker_id = :worker_id FOR UPDATE"), {"worker_id": worker_id})
            previous_row = previous.mappings().first()
            if previous_row is None:
                return None
            result = await conn.execute(sa.text("""UPDATE agent_email_identities SET state = :state, state_evidence = CAST(:evidence AS jsonb), updated_at = now()
                WHERE worker_id = :worker_id RETURNING *"""), {"worker_id": worker_id, "state": state.value, "evidence": json.dumps(redact(evidence))})
            row = result.mappings().first()
            await conn.execute(sa.text("""INSERT INTO identity_state_transitions (id, identity_id, from_state, to_state, evidence)
                VALUES (:id, :identity_id, :from_state, :to_state, CAST(:evidence AS jsonb))"""), {"id": uuid4(), "identity_id": previous_row["id"], "from_state": previous_row["state"], "to_state": state.value, "evidence": json.dumps(redact(evidence))})
            if outbox_event_type:
                await conn.execute(
                    sa.text("""INSERT INTO identity_event_outbox
                        (id, event_type, aggregate_type, aggregate_id, payload_json)
                        VALUES (:id, :event_type, 'agent_email_identity', :aggregate_id,
                                CAST(:payload AS jsonb))"""),
                    {
                        "id": uuid4(),
                        "event_type": outbox_event_type,
                        "aggregate_id": str(previous_row["id"]),
                        "payload": json.dumps(redact(outbox_payload or {})),
                    },
                )
            return dict(row) if row else None

    async def set_provider_account(self, worker_id: UUID, provider_account_id: str | None) -> dict[str, Any] | None:
        return await self._fetchone("UPDATE agent_email_identities SET provider_account_id = :provider_account_id, updated_at = now() WHERE worker_id = :worker_id RETURNING *", {"worker_id": worker_id, "provider_account_id": provider_account_id})

    async def record_email_alias(self, *, identity_id: UUID, address: str) -> dict[str, Any]:
        row = await self._fetchone(
            """INSERT INTO email_aliases (id, identity_id, address, state)
               VALUES (:id, :identity_id, :address, 'ACTIVE')
               ON CONFLICT (address) DO UPDATE SET
                 state = 'ACTIVE', updated_at = now()
               WHERE email_aliases.identity_id = EXCLUDED.identity_id
               RETURNING *""",
            {"id": uuid4(), "identity_id": identity_id, "address": address},
        )
        if row is None:
            raise ValueError("email alias is already owned by another identity")
        return row

    async def start_provisioning_job(self, *, identity_id: UUID, company_id: UUID, worker_id: UUID, idempotency_key: str) -> dict[str, Any]:
        row = await self._fetchone(
            """INSERT INTO mailbox_provisioning_jobs
                 (id, identity_id, company_id, worker_id, idempotency_key, state, attempt_count)
               VALUES (:id, :identity_id, :company_id, :worker_id, :idempotency_key, 'RUNNING', 1)
               ON CONFLICT (idempotency_key) DO UPDATE SET
                 identity_id = EXCLUDED.identity_id, state = 'RUNNING',
                 attempt_count = mailbox_provisioning_jobs.attempt_count + 1,
                 provider_correlation_id = NULL, updated_at = now()
               RETURNING *""",
            {"id": uuid4(), "identity_id": identity_id, "company_id": company_id,
             "worker_id": worker_id, "idempotency_key": idempotency_key},
        )
        assert row is not None
        return row

    async def get_provisioning_job(
        self, idempotency_key: str
    ) -> dict[str, Any] | None:
        return await self._fetchone(
            "SELECT * FROM mailbox_provisioning_jobs WHERE idempotency_key = :key",
            {"key": idempotency_key},
        )

    async def finish_provisioning_job(self, *, idempotency_key: str, state: str, provider_correlation_id: str | None, evidence: dict[str, Any]) -> dict[str, Any] | None:
        return await self._fetchone(
            """UPDATE mailbox_provisioning_jobs SET state = :state,
                 provider_correlation_id = :provider_correlation_id,
                 evidence = CAST(:evidence AS jsonb), updated_at = now()
               WHERE idempotency_key = :idempotency_key RETURNING *""",
            {"idempotency_key": idempotency_key, "state": state,
             "provider_correlation_id": provider_correlation_id,
             "evidence": json.dumps(redact(evidence))},
        )

    async def record_mail_event(self, *, identity_id: UUID, provider_message_id: str | None, event_type: str, metadata: dict[str, Any]) -> dict[str, Any]:
        row = await self._fetchone(
            """INSERT INTO mail_events
                 (id, identity_id, provider_message_id, event_type, metadata_json)
               VALUES (:id, :identity_id, :provider_message_id, :event_type, CAST(:metadata AS jsonb))
               ON CONFLICT (identity_id, provider_message_id, event_type) DO UPDATE SET
                 metadata_json = EXCLUDED.metadata_json
               RETURNING *""",
            {"id": uuid4(), "identity_id": identity_id,
             "provider_message_id": provider_message_id, "event_type": event_type,
             "metadata": json.dumps(redact(metadata))},
        )
        assert row is not None
        return row

    async def record_mail_edge_observation(self, observation: MailEdgeObservation) -> dict[str, Any]:
        existing = await self._fetchone(
            """SELECT * FROM mail_edge_observations
               WHERE provider = :provider AND event_id = :event_id""",
            {"provider": observation.provider, "event_id": observation.event_id},
        )
        if existing is not None:
            if _mail_edge_conflicts(existing, observation):
                raise ValueError("mail-edge provider event ID was reused with different data")
            return existing
        values = _mail_edge_row(observation)
        row = await self._fetchone(
            """INSERT INTO mail_edge_observations
                 (id, schema_version, provider, source, event_id, event_type,
                  outcome, failure_class, worker_id, outbound_request_id,
                  provider_message_ref, trace_id, span_id, occurred_at,
                  signature_verified, metadata_json, received_at)
               VALUES (:id, :schema_version, :provider, :source, :event_id,
                       :event_type, :outcome, :failure_class, :worker_id,
                       :outbound_request_id, :provider_message_ref, :trace_id,
                       :span_id, :occurred_at, :signature_verified,
                       CAST(:metadata AS jsonb), :received_at)
               ON CONFLICT (provider, event_id) DO NOTHING
               RETURNING *""",
            {
                "id": values["id"],
                "schema_version": values["schema_version"],
                "provider": values["provider"],
                "source": values["source"],
                "event_id": values["event_id"],
                "event_type": values["event_type"],
                "outcome": values["outcome"],
                "failure_class": values["failure_class"],
                "worker_id": values["worker_id"],
                "outbound_request_id": values["outbound_request_id"],
                "provider_message_ref": values["provider_message_ref"],
                "trace_id": values["trace_id"],
                "span_id": values["span_id"],
                "occurred_at": values["occurred_at"],
                "signature_verified": values["signature_verified"],
                "metadata": json.dumps(values["metadata"]),
                "received_at": values["received_at"],
            },
        )
        if row is not None:
            return row
        existing = await self._fetchone(
            """SELECT * FROM mail_edge_observations
               WHERE provider = :provider AND event_id = :event_id""",
            {"provider": observation.provider, "event_id": observation.event_id},
        )
        if existing is None:
            raise RuntimeError("mail-edge observation idempotency lookup failed")
        if _mail_edge_conflicts(existing, observation):
            raise ValueError("mail-edge provider event ID was reused with different data")
        return existing

    async def record_verification_transaction(self, *, identity_id: UUID, provider_message_id: str, idempotency_key: str, code_hash: str | None, link_hash: str | None, state: str) -> dict[str, Any]:
        row = await self._fetchone(
            """INSERT INTO mail_verification_transactions
                 (id, identity_id, provider_message_id, idempotency_key, code_hash, link_hash, state)
               VALUES (:id, :identity_id, :provider_message_id, :idempotency_key, :code_hash, :link_hash, :state)
               ON CONFLICT (idempotency_key) DO UPDATE SET
                 code_hash = COALESCE(EXCLUDED.code_hash, mail_verification_transactions.code_hash),
                 link_hash = COALESCE(EXCLUDED.link_hash, mail_verification_transactions.link_hash),
                 state = EXCLUDED.state, updated_at = now()
               RETURNING *""",
            {"id": uuid4(), "identity_id": identity_id,
             "provider_message_id": provider_message_id, "idempotency_key": idempotency_key,
             "code_hash": code_hash, "link_hash": link_hash, "state": state},
        )
        assert row is not None
        return row

    async def create_identity_access_grant(self, *, worker_id: UUID, identity_id: UUID, grant_type: str, issued_by: str) -> dict[str, Any]:
        row = await self._fetchone(
            """INSERT INTO identity_access_grants (id, worker_id, identity_id, grant_type, state, issued_by)
               VALUES (:id, :worker_id, :identity_id, :grant_type, 'ACTIVE', :issued_by)
               ON CONFLICT (worker_id, identity_id, grant_type) DO UPDATE
               SET state = 'ACTIVE', updated_at = now()
               RETURNING *""",
            {"id": uuid4(), "worker_id": worker_id, "identity_id": identity_id,
             "grant_type": grant_type, "issued_by": issued_by},
        )
        assert row is not None
        return row

    async def has_identity_access_grant(self, *, worker_id: UUID, identity_id: UUID, grant_type: str) -> bool:
        row = await self._fetchone(
            """SELECT 1 FROM identity_access_grants
               WHERE worker_id = :worker_id AND identity_id = :identity_id
                 AND grant_type = :grant_type AND state = 'ACTIVE'""",
            {"worker_id": worker_id, "identity_id": identity_id, "grant_type": grant_type},
        )
        return row is not None

    async def create_outbox(self, event_type: str, aggregate_type: str, aggregate_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        row = await self._fetchone(
            """INSERT INTO identity_event_outbox (id, event_type, aggregate_type, aggregate_id, payload_json)
               VALUES (:id, :event_type, :aggregate_type, :aggregate_id, CAST(:payload AS jsonb)) RETURNING *""",
            {"id": uuid4(), "event_type": event_type, "aggregate_type": aggregate_type, "aggregate_id": aggregate_id, "payload": json.dumps(redact(payload))},
        )
        assert row is not None
        return row

    async def list_outbox(self, cursor: int, limit: int) -> list[dict[str, Any]]:
        async with self.engine.connect() as conn:
            result = await conn.execute(sa.text("SELECT * FROM identity_event_outbox WHERE sequence > :cursor ORDER BY sequence ASC LIMIT :limit"), {"cursor": cursor, "limit": limit})
            return [dict(item) for item in result.mappings().all()]

    async def get_max_outbox_sequence(self) -> int:
        async with self.engine.connect() as conn:
            value = await conn.scalar(
                sa.text("SELECT COALESCE(MAX(sequence), 0) FROM identity_event_outbox")
            )
        return int(value or 0)

    async def get_client_cursor(self, client_id: str) -> int:
        row = await self._fetchone(
            "SELECT last_sequence FROM identity_client_cursors WHERE client_id = :client_id",
            {"client_id": client_id},
        )
        return int(row["last_sequence"]) if row else 0

    async def advance_client_cursor(self, client_id: str, cursor: int) -> int:
        row = await self._fetchone(
            """INSERT INTO identity_client_cursors (id, client_id, last_sequence)
               VALUES (:id, :client_id, :cursor)
               ON CONFLICT (client_id) DO UPDATE SET
                 last_sequence = GREATEST(identity_client_cursors.last_sequence, EXCLUDED.last_sequence),
                 updated_at = now()
               RETURNING last_sequence""",
            {"id": uuid4(), "client_id": client_id, "cursor": cursor},
        )
        assert row is not None
        return int(row["last_sequence"])

    async def consume_provider_rate(self, *, provider: str, rate_key: str, window_started_at: datetime, limit: int) -> bool:
        row = await self._fetchone(
            """INSERT INTO identity_provider_rates
                 (id, provider, rate_key, window_started_at, count)
               VALUES (:id, :provider, :rate_key, :window_started_at, 1)
               ON CONFLICT (provider, rate_key, window_started_at) DO UPDATE SET
                 count = identity_provider_rates.count + 1
               WHERE identity_provider_rates.count < :limit
               RETURNING count""",
            {"id": uuid4(), "provider": provider, "rate_key": rate_key,
             "window_started_at": window_started_at, "limit": limit},
        )
        return row is not None

    async def create_audit(self, **kwargs: Any) -> dict[str, Any]:
        row = await self._fetchone(
            """INSERT INTO identity_audit_events (id, actor_id, action, target_type, target_id, outcome, metadata_json)
               VALUES (:id, :actor_id, :action, :target_type, :target_id, :outcome, CAST(:metadata AS jsonb)) RETURNING *""",
            {"id": uuid4(), "actor_id": kwargs["actor_id"], "action": kwargs["action"], "target_type": kwargs["target_type"], "target_id": str(kwargs["target_id"]), "outcome": kwargs["outcome"], "metadata": json.dumps(redact(kwargs.get("metadata", {})))},
        )
        assert row is not None
        return row

    async def create_approval(self, *, worker_id: UUID, kind: str, target_id: UUID, idempotency_key: str) -> dict[str, Any]:
        row = await self._fetchone(
            """INSERT INTO identity_approval_requests (id, worker_id, kind, target_id, idempotency_key, state)
               VALUES (:id, :worker_id, :kind, :target_id, :idempotency_key, 'PENDING')
               ON CONFLICT (idempotency_key) DO NOTHING RETURNING *""",
            {"id": uuid4(), "worker_id": worker_id, "kind": kind, "target_id": target_id, "idempotency_key": idempotency_key},
        )
        if row:
            return row
        existing = await self._fetchone("SELECT * FROM identity_approval_requests WHERE idempotency_key = :key", {"key": idempotency_key})
        if existing is None:
            raise RuntimeError("approval idempotency lookup failed")
        return existing

    async def decide_approval(self, approval_id: UUID, state: ApprovalState, actor_id: str, reason: str) -> dict[str, Any] | None:
        return await self._fetchone(
            """UPDATE identity_approval_requests SET state = :state, decided_by = :actor_id, reason = :reason, decided_at = now()
               WHERE id = :id AND state = 'PENDING' RETURNING *""",
            {"id": approval_id, "state": state.value, "actor_id": actor_id, "reason": reason},
        )

    async def get_approval_for_target(self, target_id: UUID) -> dict[str, Any] | None:
        return await self._fetchone("SELECT * FROM identity_approval_requests WHERE target_id = :target_id ORDER BY created_at DESC LIMIT 1", {"target_id": target_id})

    def _encrypt_body(self, body: str) -> str:
        if self._content_fernet is None:
            raise RuntimeError("IDENTITY_CONTENT_ENCRYPTION_KEY is required for durable outbound content")
        return self._content_fernet.encrypt(body.encode()).decode()

    def _decrypt_body(self, encrypted: str) -> str:
        if self._content_fernet is None:
            raise RuntimeError("identity outbound content decryption key unavailable")
        return self._content_fernet.decrypt(encrypted.encode()).decode()

    @staticmethod
    def _json(value: Any) -> str:
        return json.dumps(value, default=str)

    async def create_outbound_request(self, **kwargs: Any) -> tuple[dict[str, Any], bool]:
        import hashlib

        body = str(kwargs.pop("body"))
        encrypted_content = self._encrypt_body(body)
        row = await self._fetchone(
            """INSERT INTO outbound_mail_requests
                 (id, identity_id, worker_id, sender, recipients_json, recipient_class, subject, content_ref, content_hash, state, idempotency_key)
               VALUES (:id, :identity_id, :worker_id, :sender, CAST(:recipients AS jsonb), :recipient_class, :subject, :content_ref, :content_hash, 'PENDING_APPROVAL', :idempotency_key)
               ON CONFLICT (idempotency_key) DO NOTHING RETURNING *""",
            {"id": uuid4(), "identity_id": kwargs["identity_id"], "worker_id": kwargs["worker_id"], "sender": kwargs["sender"], "recipients": self._json(kwargs["recipients"]), "recipient_class": kwargs["recipient_class"], "subject": kwargs["subject"], "content_ref": encrypted_content, "content_hash": hashlib.sha256(body.encode()).hexdigest(), "idempotency_key": kwargs["idempotency_key"]},
        )
        if row is not None:
            row["recipients"] = row.pop("recipients_json", [])
            row["body"] = body
            return row, True
        existing = await self.get_outbound_request_by_key(str(kwargs["idempotency_key"]))
        if existing is None:
            raise RuntimeError("outbound idempotency lookup failed")
        return existing, False

    async def get_outbound_request_by_key(self, idempotency_key: str) -> dict[str, Any] | None:
        row = await self._fetchone("SELECT * FROM outbound_mail_requests WHERE idempotency_key = :key", {"key": idempotency_key})
        return self._hydrate_outbound(row)

    def _hydrate_outbound(self, row: dict[str, Any] | None) -> dict[str, Any] | None:
        if row is None:
            return None
        recipients = row.pop("recipients_json", row.get("recipients", []))
        row["recipients"] = recipients if isinstance(recipients, list) else json.loads(recipients)
        row["body"] = self._decrypt_body(str(row.pop("content_ref")))
        return row

    async def get_outbound_request(self, request_id: UUID) -> dict[str, Any] | None:
        return self._hydrate_outbound(await self._fetchone("SELECT * FROM outbound_mail_requests WHERE id = :id", {"id": request_id}))

    async def get_outbound_request_metadata(self, request_id: UUID) -> dict[str, Any] | None:
        return await self._fetchone(
            """SELECT id, identity_id, worker_id, provider_message_id,
                      provider_correlation_id, state
                 FROM outbound_mail_requests WHERE id = :id""",
            {"id": request_id},
        )

    async def find_outbound_request_by_provider_message_id(self, provider_message_id: str) -> dict[str, Any] | None:
        return await self._fetchone(
            """SELECT id, identity_id, worker_id, provider_message_id,
                      provider_correlation_id, state
                 FROM outbound_mail_requests
                 WHERE provider_message_id = :provider_message_id""",
            {"provider_message_id": provider_message_id},
        )

    async def claim_outbound_submission(self, request_id: UUID) -> tuple[dict[str, Any] | None, bool]:
        row = await self._fetchone(
            """UPDATE outbound_mail_requests SET state = 'SUBMITTING', updated_at = now()
               WHERE id = :id AND state IN ('PENDING_APPROVAL', 'SUBMISSION_FAILED')
               RETURNING *""",
            {"id": request_id},
        )
        if row is not None:
            return self._hydrate_outbound(row), True
        return await self.get_outbound_request(request_id), False

    async def update_outbound_request(self, request_id: UUID, **values: Any) -> dict[str, Any] | None:
        # Only whitelisted lifecycle/provider metadata can change after creation.
        allowed = {key: value for key, value in values.items() if key in {"state", "provider_message_id", "provider_correlation_id"}}
        if not allowed:
            return await self.get_outbound_request(request_id)
        assignments = [f"{key} = :{key}" for key in allowed]
        row = await self._fetchone(f"UPDATE outbound_mail_requests SET {', '.join(assignments)}, updated_at = now() WHERE id = :id RETURNING *", {"id": request_id, **allowed})
        return self._hydrate_outbound(row)

    async def record_delivery_attempt(self, *, outbound_request_id: UUID, provider_correlation_id: str | None, provider_message_id: str | None, outcome: str, failure_class: str | None = None, sanitized_reason: str | None = None, trace_id: str | None = None, span_id: str | None = None) -> dict[str, Any]:
        async with self.engine.begin() as conn:
            # Lock the parent row so parallel retries receive unique attempt
            # numbers as well as a durable delivery sequence.
            await conn.execute(
                sa.text("SELECT id FROM outbound_mail_requests WHERE id = :id FOR UPDATE"),
                {"id": outbound_request_id},
            )
            result = await conn.execute(
                sa.text("""INSERT INTO outbound_delivery_attempts
                             (id, outbound_request_id, attempt_number, provider_correlation_id,
                              provider_message_id, outcome, failure_class, sanitized_reason,
                              trace_id, span_id)
                           VALUES (:id, :outbound_request_id,
                             (SELECT COALESCE(MAX(attempt_number), 0) + 1
                                FROM outbound_delivery_attempts WHERE outbound_request_id = :outbound_request_id),
                             :provider_correlation_id, :provider_message_id, :outcome,
                             :failure_class, :sanitized_reason, :trace_id, :span_id)
                           RETURNING *"""),
                {"id": uuid4(), "outbound_request_id": outbound_request_id,
                 "provider_correlation_id": provider_correlation_id,
                 "provider_message_id": provider_message_id, "outcome": outcome,
                 "failure_class": failure_class, "sanitized_reason": sanitized_reason,
                 "trace_id": normalize_trace_id(trace_id),
                 "span_id": normalize_span_id(span_id) or (new_span_id() if normalize_trace_id(trace_id) else None)},
            )
            row = result.mappings().first()
        assert row is not None
        return dict(row)

    async def create_external_account(self, **kwargs: Any) -> tuple[dict[str, Any], bool]:
        values = dict(kwargs)
        account_id = values.pop("account_id")
        values["identity_id"] = values.pop("email_identity_id", None)
        row = await self._fetchone(
            """INSERT INTO external_accounts (id, worker_id, identity_id, service, service_category, state, credential_ref, browser_profile_ref, approval_id, idempotency_key)
               VALUES (:id, :worker_id, :identity_id, :service, :service_category, 'REQUESTED', :credential_ref, :browser_profile_ref, :approval_id, :idempotency_key)
               ON CONFLICT (idempotency_key) DO NOTHING RETURNING *""",
            {"id": account_id, **values},
        )
        if row:
            return row, True
        existing = await self._fetchone("SELECT * FROM external_accounts WHERE idempotency_key = :key", {"key": values["idempotency_key"]})
        if existing is None:
            raise RuntimeError("external-account idempotency lookup failed")
        return existing, False

    async def get_external_account(self, account_id: UUID) -> dict[str, Any] | None:
        return await self._fetchone("SELECT * FROM external_accounts WHERE id = :id", {"id": account_id})

    async def bind_external_account(self, account_id: UUID, *, approval_id: UUID, credential_ref: str) -> dict[str, Any] | None:
        return await self._fetchone(
            """UPDATE external_accounts
               SET approval_id = :approval_id, credential_ref = :credential_ref,
                   updated_at = now()
               WHERE id = :id RETURNING *""",
            {"id": account_id, "approval_id": approval_id,
             "credential_ref": credential_ref},
        )

    async def update_external_account(self, account_id: UUID, state: ExternalAccountState) -> dict[str, Any] | None:
        return await self._fetchone("UPDATE external_accounts SET state = :state, updated_at = now() WHERE id = :id RETURNING *", {"id": account_id, "state": state.value})

    async def suspend_external_accounts(self, worker_id: UUID) -> int:
        async with self.engine.begin() as conn:
            result = await conn.execute(
                sa.text("""UPDATE external_accounts SET state = 'SUSPENDED', updated_at = now()
                           WHERE worker_id = :worker_id AND state <> 'CLOSED'"""),
                {"worker_id": worker_id},
            )
            return result.rowcount or 0

    async def create_credential_lease(self, *, external_account_id: UUID, worker_id: UUID, lease_hash: str, scope: str, expires_at: datetime) -> dict[str, Any]:
        row = await self._fetchone(
            """INSERT INTO credential_leases
                 (id, external_account_id, worker_id, lease_hash, scope, state, expires_at)
               VALUES (:id, :external_account_id, :worker_id, :lease_hash, :scope, 'ACTIVE', :expires_at)
               ON CONFLICT (lease_hash) DO NOTHING RETURNING *""",
            {"id": uuid4(), "external_account_id": external_account_id,
             "worker_id": worker_id, "lease_hash": lease_hash, "scope": scope,
             "expires_at": expires_at},
        )
        if row is not None:
            return row
        existing = await self._fetchone(
            "SELECT * FROM credential_leases WHERE lease_hash = :lease_hash",
            {"lease_hash": lease_hash},
        )
        if existing is None:
            raise RuntimeError("credential lease persistence failed")
        return existing

    async def consume_credential_lease(self, *, external_account_id: UUID, worker_id: UUID, lease_hash: str, scope: str) -> bool:
        row = await self._fetchone(
            """UPDATE credential_leases
               SET state = 'CONSUMED', used_at = now()
               WHERE external_account_id = :external_account_id
                 AND worker_id = :worker_id AND lease_hash = :lease_hash
                 AND scope = :scope AND state = 'ACTIVE'
                 AND used_at IS NULL AND expires_at > now()
               RETURNING id""",
            {"external_account_id": external_account_id,
             "worker_id": worker_id, "lease_hash": lease_hash,
             "scope": scope},
        )
        return row is not None

    async def create_browser_session(self, **kwargs: Any) -> tuple[dict[str, Any], bool]:
        row = await self._fetchone(
            """INSERT INTO browser_auth_sessions (id, worker_id, external_account_id, service, profile_ref, state, idempotency_key)
               VALUES (:id, :worker_id, :external_account_id, :service, :profile_ref, 'ACTIVE', :idempotency_key)
               ON CONFLICT (idempotency_key) DO NOTHING RETURNING *""",
            {"id": uuid4(), **kwargs},
        )
        if row:
            return row, True
        existing = await self._fetchone("SELECT * FROM browser_auth_sessions WHERE idempotency_key = :key", {"key": kwargs["idempotency_key"]})
        if existing is None:
            raise RuntimeError("browser session idempotency lookup failed")
        return existing, False

    async def get_browser_session(self, session_id: UUID) -> dict[str, Any] | None:
        return await self._fetchone("SELECT * FROM browser_auth_sessions WHERE id = :id", {"id": session_id})

    async def revoke_browser_sessions(self, worker_id: UUID) -> int:
        async with self.engine.begin() as conn:
            result = await conn.execute(sa.text("UPDATE browser_auth_sessions SET state = 'REVOKED', lease_version = lease_version + 1, updated_at = now() WHERE worker_id = :worker_id AND state = 'ACTIVE'"), {"worker_id": worker_id})
            return result.rowcount or 0

    async def reserve_hold(self, *, worker_id: UUID, kind: str, idempotency_key: str, units: int) -> tuple[dict[str, Any], bool]:
        row = await self._fetchone(
            """INSERT INTO identity_budget_holds (id, worker_id, kind, units, state, idempotency_key)
               VALUES (:id, :worker_id, :kind, :units, 'RESERVED', :idempotency_key)
               ON CONFLICT (idempotency_key) DO UPDATE SET
                 state = 'RESERVED', updated_at = now()
               WHERE identity_budget_holds.state = 'RELEASED'
                 AND identity_budget_holds.worker_id = EXCLUDED.worker_id
                 AND identity_budget_holds.kind = EXCLUDED.kind
                 AND identity_budget_holds.units = EXCLUDED.units
               RETURNING *""",
            {"id": uuid4(), "worker_id": worker_id, "kind": kind, "units": units, "idempotency_key": idempotency_key},
        )
        if row:
            return row, True
        existing = await self._fetchone("SELECT * FROM identity_budget_holds WHERE idempotency_key = :key", {"key": idempotency_key})
        if existing is None:
            raise RuntimeError("budget hold idempotency lookup failed")
        if (
            existing["worker_id"] != worker_id
            or existing["kind"] != kind
            or int(existing["units"]) != units
        ):
            raise ValueError("identity budget hold idempotency key was reused with different inputs")
        return existing, False

    async def settle_hold(self, hold_id: UUID, state: UsageHoldState) -> dict[str, Any] | None:
        async with self.engine.begin() as conn:
            result = await conn.execute(
                sa.text("""UPDATE identity_budget_holds
                           SET state = :state, updated_at = now()
                           WHERE id = :id AND state = 'RESERVED'
                           RETURNING *"""),
                {"id": hold_id, "state": state.value},
            )
            mapped = result.mappings().first()
            if mapped is None:
                return None
            row = dict(mapped)
            if state == UsageHoldState.COMMITTED:
                await conn.execute(
                    sa.text("""INSERT INTO identity_usage_events
                          (id, worker_id, kind, units, hold_id, idempotency_key)
                        VALUES (:id, :worker_id, :kind, :units, :hold_id,
                                :idempotency_key)
                        ON CONFLICT (idempotency_key) DO NOTHING"""),
                    {"id": uuid4(), "worker_id": row["worker_id"],
                     "kind": row["kind"], "units": row["units"],
                     "hold_id": hold_id,
                     "idempotency_key": f"commit:{row['idempotency_key']}"},
                )
            return row

    async def dashboard_rows(self, resource: str, limit: int = 100) -> list[dict[str, Any]]:
        queries = {
            "identities": "SELECT * FROM agent_email_identities ORDER BY updated_at DESC LIMIT :limit",
            "mailboxes": """SELECT i.*, COALESCE((SELECT jsonb_agg(a.address ORDER BY a.address) FROM email_aliases a WHERE a.identity_id = i.id AND a.state = 'ACTIVE'), '[]'::jsonb) AS aliases FROM agent_email_identities i ORDER BY i.updated_at DESC LIMIT :limit""",
            "outbound-mail": """SELECT r.id, r.worker_id, r.identity_id, r.sender, r.recipients_json, r.recipient_class, r.subject, r.state, r.provider_message_id, r.provider_correlation_id, a.state AS approval_state, r.created_at, r.updated_at FROM outbound_mail_requests r LEFT JOIN LATERAL (SELECT state FROM identity_approval_requests WHERE target_id = r.id ORDER BY created_at DESC LIMIT 1) a ON true ORDER BY r.updated_at DESC LIMIT :limit""",
            "external-accounts": "SELECT * FROM external_accounts ORDER BY updated_at DESC LIMIT :limit",
            "auth-sessions": "SELECT id, worker_id, external_account_id, service, state, lease_version, created_at, updated_at FROM browser_auth_sessions ORDER BY updated_at DESC LIMIT :limit",
            "identity-approvals": "SELECT * FROM identity_approval_requests ORDER BY created_at DESC LIMIT :limit",
            "identity-audit": "SELECT * FROM identity_audit_events ORDER BY occurred_at DESC LIMIT :limit",
            "mail-domains": "SELECT * FROM email_domains ORDER BY updated_at DESC LIMIT :limit",
            "mail-edge": """SELECT o.id, o.outbound_request_id, o.provider_message_ref AS provider_message_id,
                              NULL::text AS provider_correlation_id, o.outcome,
                              o.failure_class, NULL::text AS sanitized_reason,
                              o.trace_id, o.span_id, o.occurred_at AS attempted_at,
                              o.occurred_at, o.source, o.event_type,
                              o.signature_verified, i.provider_account_id
                         FROM mail_edge_observations o
                         LEFT JOIN outbound_mail_requests r ON r.id::text = o.outbound_request_id
                         LEFT JOIN agent_email_identities i ON i.id = r.identity_id
                        ORDER BY o.occurred_at DESC LIMIT :limit""",
            "mail-relay": """SELECT * FROM (
                              SELECT d.id, d.outbound_request_id::text AS outbound_request_id,
                                     d.provider_message_id, d.provider_correlation_id,
                                     d.outcome, d.failure_class, d.sanitized_reason,
                                     d.trace_id, d.span_id, d.attempted_at,
                                     d.attempted_at AS occurred_at,
                                     'delivery_attempt'::text AS source,
                                     NULL::text AS event_type,
                                     NULL::boolean AS signature_verified,
                                     i.provider_account_id
                                FROM outbound_delivery_attempts d
                                JOIN outbound_mail_requests r ON r.id = d.outbound_request_id
                                JOIN agent_email_identities i ON i.id = r.identity_id
                              UNION ALL
                              SELECT o.id, o.outbound_request_id,
                                     o.provider_message_ref AS provider_message_id,
                                     NULL::text AS provider_correlation_id,
                                     o.outcome, o.failure_class,
                                     NULL::text AS sanitized_reason,
                                     o.trace_id, o.span_id,
                                     o.occurred_at AS attempted_at,
                                     o.occurred_at, o.source, o.event_type,
                                     o.signature_verified, i.provider_account_id
                                FROM mail_edge_observations o
                                LEFT JOIN outbound_mail_requests r ON r.id::text = o.outbound_request_id
                                LEFT JOIN agent_email_identities i ON i.id = r.identity_id
                           ) relay
                         ORDER BY relay.occurred_at DESC LIMIT :limit""",
        }
        statement = queries.get(resource)
        if statement is None:
            return []
        async with self.engine.connect() as conn:
            result = await conn.execute(sa.text(statement), {"limit": limit})
            return [dict(item) for item in result.mappings().all()]

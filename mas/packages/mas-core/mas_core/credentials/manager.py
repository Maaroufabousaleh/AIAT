"""CredentialsManager — encrypted secret store backed by Postgres.

All secrets are encrypted with AES-128-CBC via the ``cryptography`` Fernet
symmetric key scheme before writing to the database.  The encryption key
itself comes from the environment variable ``CREDENTIALS_ENCRYPTION_KEY``
(32-byte URL-safe base64 value, generated once and stored outside the DB).

API
---
The manager exposes:

* ``create``  — store a new named secret
* ``update``  — replace the encrypted value or policy
* ``resolve`` — decrypt and return the real value (policy-gated + audited)
* ``list``    — return all ``SecretMetadata`` objects (no values)
* ``get``     — return one ``SecretMetadata`` by name
* ``delete``  — remove a secret record
* ``audit_log`` — return recent resolve audit entries
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import sqlalchemy as sa

from .models import SecretMetadata, SecretPolicy, SecretType

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Encryption helpers (pure-Python AES via cryptography.fernet)
# ---------------------------------------------------------------------------


def _get_fernet():
    """Lazily import Fernet and return an instance using the env key."""
    try:
        from cryptography.fernet import Fernet
    except ImportError as exc:
        raise RuntimeError(
            "The 'cryptography' package is required for CredentialsManager. "
            "Run: pip install cryptography"
        ) from exc

    raw = os.getenv("CREDENTIALS_ENCRYPTION_KEY", "")
    if not raw:
        environment = os.getenv("MAS_ENVIRONMENT", "development").strip().lower()
        if environment in {"production", "prod", "staging"}:
            raise RuntimeError(
                "CREDENTIALS_ENCRYPTION_KEY is required when MAS_ENVIRONMENT is "
                f"{environment!r}; refusing to derive a production key"
            )
        # Derive a deterministic dev key from a fixed seed — NOT for production.
        seed = b"aiat-dev-credentials-key-do-not-use-in-prod"
        raw = base64.urlsafe_b64encode(hashlib.sha256(seed).digest()).decode()
        logger.warning(
            "CREDENTIALS_ENCRYPTION_KEY not set — using derived dev key. "
            "Set a strong random key in production."
        )
    return Fernet(raw.encode() if isinstance(raw, str) else raw)


def _encrypt(value: str) -> str:
    f = _get_fernet()
    return f.encrypt(value.encode()).decode()


def _decrypt(token: str) -> str:
    f = _get_fernet()
    return f.decrypt(token.encode()).decode()


# ---------------------------------------------------------------------------
# SQL helpers
# ---------------------------------------------------------------------------

_CREATE_TABLE = sa.text("""
CREATE TABLE IF NOT EXISTS credentials (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name            TEXT NOT NULL UNIQUE,
    description     TEXT NOT NULL DEFAULT '',
    secret_type     TEXT NOT NULL DEFAULT 'other',
    encrypted_value TEXT NOT NULL,
    policy_json     JSONB NOT NULL DEFAULT '{}',
    usage_count     BIGINT NOT NULL DEFAULT 0,
    last_used_at    TIMESTAMPTZ,
    created_by      TEXT NOT NULL DEFAULT 'system',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
)
""")

_CREATE_AUDIT_TABLE = sa.text("""
CREATE TABLE IF NOT EXISTS credentials_audit (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    secret_name  TEXT NOT NULL,
    requester    TEXT NOT NULL,
    context      TEXT NOT NULL,
    allowed      BOOLEAN NOT NULL,
    reason       TEXT NOT NULL DEFAULT '',
    resolved_at  TIMESTAMPTZ NOT NULL DEFAULT now()
)
""")

_CREATE_APPROVAL_TABLE = sa.text("""
CREATE TABLE IF NOT EXISTS credential_resolve_approvals (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    secret_name     TEXT NOT NULL REFERENCES credentials(name) ON DELETE CASCADE,
    requester       TEXT NOT NULL,
    context         TEXT NOT NULL,
    state           TEXT NOT NULL DEFAULT 'PENDING',
    requested_by    TEXT NOT NULL,
    decided_by      TEXT,
    decision_reason TEXT NOT NULL DEFAULT '',
    expires_at      TIMESTAMPTZ NOT NULL,
    decided_at      TIMESTAMPTZ,
    consumed_at     TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT ck_credential_resolve_approval_state
        CHECK (state IN ('PENDING', 'APPROVED', 'REJECTED', 'CONSUMED', 'EXPIRED'))
)
""")

_CREATE_RATE_TABLE = sa.text("""
CREATE TABLE IF NOT EXISTS credential_resolve_rates (
    secret_name       TEXT NOT NULL REFERENCES credentials(name) ON DELETE CASCADE,
    requester         TEXT NOT NULL,
    window_started_at TIMESTAMPTZ NOT NULL,
    resolve_count     INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (secret_name, requester, window_started_at),
    CONSTRAINT ck_credential_resolve_rate_count CHECK (resolve_count >= 0)
)
""")


# ---------------------------------------------------------------------------
# CredentialsManager
# ---------------------------------------------------------------------------


class CredentialsManager:
    """Centralised secret store.

    Parameters
    ----------
    conn_factory:
        Async callable that returns an ``AsyncConnection``.  Typically
        ``engine.begin`` or ``engine.connect``.
    """

    def __init__(self, conn_factory: Any) -> None:
        self._conn_factory = conn_factory

    async def ensure_tables(self) -> None:
        """Create the credentials and audit tables if they do not exist."""
        async with self._conn_factory() as conn:
            await conn.execute(_CREATE_TABLE)
            await conn.execute(_CREATE_AUDIT_TABLE)
            await conn.execute(_CREATE_APPROVAL_TABLE)
            await conn.execute(_CREATE_RATE_TABLE)
            await conn.commit()

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------

    async def create(
        self,
        name: str,
        value: str,
        *,
        description: str = "",
        secret_type: str | SecretType = SecretType.OTHER,
        policy: SecretPolicy | None = None,
        created_by: str = "system",
    ) -> SecretMetadata:
        """Store a new named secret."""
        if policy is None:
            policy = SecretPolicy()
        encrypted = _encrypt(value)
        now = datetime.now(UTC)
        sid = uuid4()
        async with self._conn_factory() as conn:
            await conn.execute(
                sa.text("""
                    INSERT INTO credentials
                        (id, name, description, secret_type, encrypted_value,
                         policy_json, created_by, created_at, updated_at)
                    VALUES
                        (:id, :name, :desc, :stype, :enc,
                         :policy, :created_by, :now, :now)
                """),
                {
                    "id": str(sid),
                    "name": name,
                    "desc": description,
                    "stype": str(secret_type),
                    "enc": encrypted,
                    "policy": json.dumps(policy.model_dump(mode="json")),
                    "created_by": created_by,
                    "now": now,
                },
            )
            await conn.commit()
        logger.info("credentials.created name=%s by=%s", name, created_by)
        return SecretMetadata(
            id=sid,
            name=name,
            description=description,
            secret_type=SecretType(str(secret_type)),
            policy=policy,
            created_by=created_by,
            created_at=now,
            updated_at=now,
        )

    async def update(
        self,
        name: str,
        *,
        value: str | None = None,
        description: str | None = None,
        policy: SecretPolicy | None = None,
    ) -> SecretMetadata | None:
        """Update an existing secret's value and/or policy."""
        async with self._conn_factory() as conn:
            sets = ["updated_at = :now"]
            params: dict[str, Any] = {"name": name, "now": datetime.now(UTC)}
            if value is not None:
                sets.append("encrypted_value = :enc")
                params["enc"] = _encrypt(value)
            if description is not None:
                sets.append("description = :desc")
                params["desc"] = description
            if policy is not None:
                sets.append("policy_json = :policy")
                params["policy"] = json.dumps(policy.model_dump(mode="json"))

            await conn.execute(
                sa.text(f"UPDATE credentials SET {', '.join(sets)} WHERE name = :name"),
                params,
            )
            await conn.commit()
        return await self.get(name)

    async def delete(self, name: str) -> bool:
        """Remove a secret."""
        async with self._conn_factory() as conn:
            result = await conn.execute(
                sa.text("DELETE FROM credentials WHERE name = :name RETURNING id"),
                {"name": name},
            )
            await conn.commit()
            return result.rowcount > 0

    # ------------------------------------------------------------------
    # Read / resolve
    # ------------------------------------------------------------------

    async def list(self) -> list[SecretMetadata]:
        """Return metadata for all secrets (no values)."""
        async with self._conn_factory() as conn:
            rows = await conn.execute(
                sa.text("""
                    SELECT id, name, description, secret_type, policy_json,
                           usage_count, last_used_at, created_by, created_at, updated_at
                    FROM credentials ORDER BY created_at DESC
                """)
            )
            return [self._row_to_meta(r) for r in rows.mappings().all()]

    async def get(self, name: str) -> SecretMetadata | None:
        """Return metadata for a single secret by name."""
        async with self._conn_factory() as conn:
            row = await conn.execute(
                sa.text("""
                    SELECT id, name, description, secret_type, policy_json,
                           usage_count, last_used_at, created_by, created_at, updated_at
                    FROM credentials WHERE name = :name
                """),
                {"name": name},
            )
            r = row.mappings().first()
            return self._row_to_meta(r) if r else None

    async def resolve(
        self,
        name: str,
        *,
        requester: str = "anonymous",
        context: str = "default",
        approval_id: UUID | str | None = None,
    ) -> str | None:
        """Resolve a secret reference to its real value.

        Returns ``None`` if the secret does not exist or the policy denies
        access.  The resolution attempt is always written to the audit log.
        """
        async with self._conn_factory() as conn:
            row = await conn.execute(
                sa.text("""
                    SELECT id, name, encrypted_value, policy_json
                    FROM credentials WHERE name = :name
                """),
                {"name": name},
            )
            r = row.mappings().first()

        if r is None:
            await self._audit(name, requester, context, False, "secret_not_found")
            return None

        policy_raw = (
            r["policy_json"] if isinstance(r["policy_json"], dict) else json.loads(r["policy_json"])
        )
        policy = SecretPolicy.model_validate(policy_raw)
        allowed, reason = policy.allows(requester, context)

        if not allowed:
            await self._audit(name, requester, context, False, reason)
            logger.warning(
                "credentials.resolve_denied name=%s requester=%s reason=%s",
                name,
                requester,
                reason,
            )
            return None

        if policy.rate_limit_per_minute:
            rate_allowed = await self._consume_rate_limit(
                name=name,
                requester=requester,
                limit=policy.rate_limit_per_minute,
            )
            if not rate_allowed:
                await self._audit(
                    name, requester, context, False, "rate_limit_exceeded"
                )
                logger.warning(
                    "credentials.resolve_denied name=%s requester=%s reason=rate_limit_exceeded",
                    name,
                    requester,
                )
                return None

        if policy.require_approval:
            approved = await self._consume_approval(
                approval_id=approval_id,
                name=name,
                requester=requester,
                context=context,
            )
            if not approved:
                await self._audit(
                    name, requester, context, False, "valid_approval_required"
                )
                logger.warning(
                    "credentials.resolve_denied name=%s requester=%s reason=valid_approval_required",
                    name,
                    requester,
                )
                return None

        # An allowed resolve must never return a secret unless its durable
        # audit record was committed successfully.  This is intentionally
        # fail-closed; the caller can retry after the database recovers.
        await self._audit(name, requester, context, True, "ok", strict=True)

        # Increment usage counter
        async with self._conn_factory() as conn:
            await conn.execute(
                sa.text("""
                    UPDATE credentials
                    SET usage_count = usage_count + 1, last_used_at = :now
                    WHERE name = :name
                """),
                {"name": name, "now": datetime.now(UTC)},
            )
            await conn.commit()

        return _decrypt(r["encrypted_value"])

    async def request_approval(
        self,
        name: str,
        *,
        requester: str,
        context: str,
        requested_by: str,
        ttl_seconds: int = 900,
    ) -> dict[str, Any]:
        """Create a one-use, short-lived approval request for a secret resolve."""
        if ttl_seconds < 60 or ttl_seconds > 3600:
            raise ValueError("credential approval TTL must be between 60 and 3600 seconds")
        metadata = await self.get(name)
        if metadata is None:
            raise LookupError("credential not found")
        if not metadata.policy.require_approval:
            raise ValueError("credential policy does not require per-use approval")
        approval_id = uuid4()
        now = datetime.now(UTC)
        async with self._conn_factory() as conn:
            result = await conn.execute(
                sa.text("""
                    INSERT INTO credential_resolve_approvals
                        (id, secret_name, requester, context, state, requested_by,
                         expires_at, created_at)
                    VALUES
                        (:id, :name, :requester, :context, 'PENDING', :requested_by,
                         :expires_at, :created_at)
                    RETURNING id, secret_name, requester, context, state,
                              requested_by, expires_at, created_at
                """),
                {
                    "id": approval_id,
                    "name": name,
                    "requester": requester,
                    "context": context,
                    "requested_by": requested_by,
                    "expires_at": now + timedelta(seconds=ttl_seconds),
                    "created_at": now,
                },
            )
            row = result.mappings().first()
            await conn.commit()
        if row is None:
            raise RuntimeError("credential approval request was not persisted")
        return dict(row)

    async def decide_approval(
        self,
        approval_id: UUID,
        *,
        approved: bool,
        decided_by: str,
        reason: str = "",
    ) -> dict[str, Any] | None:
        """Record a human decision without exposing the underlying secret."""
        state = "APPROVED" if approved else "REJECTED"
        async with self._conn_factory() as conn:
            result = await conn.execute(
                sa.text("""
                    UPDATE credential_resolve_approvals
                    SET state = :state, decided_by = :decided_by,
                        decision_reason = :reason, decided_at = now()
                    WHERE id = :id AND state = 'PENDING' AND expires_at > now()
                    RETURNING id, secret_name, requester, context, state,
                              requested_by, decided_by, decision_reason,
                              expires_at, decided_at, consumed_at, created_at
                """),
                {
                    "id": approval_id,
                    "state": state,
                    "decided_by": decided_by,
                    "reason": reason[:2000],
                },
            )
            row = result.mappings().first()
            await conn.commit()
        return dict(row) if row else None

    async def audit_log(
        self, limit: int = 100, secret_name: str | None = None
    ) -> list[dict[str, Any]]:
        """Return recent credential resolve audit entries.

        Args:
            limit: Maximum number of rows to return.
            secret_name: If provided, filter to entries for this credential only.
        """
        async with self._conn_factory() as conn:
            if secret_name is not None:
                rows = await conn.execute(
                    sa.text("""
                        SELECT id, secret_name, requester, context, allowed, reason, resolved_at
                        FROM credentials_audit
                        WHERE secret_name = :name
                        ORDER BY resolved_at DESC LIMIT :lim
                    """),
                    {"lim": limit, "name": secret_name},
                )
            else:
                rows = await conn.execute(
                    sa.text("""
                        SELECT id, secret_name, requester, context, allowed, reason, resolved_at
                        FROM credentials_audit ORDER BY resolved_at DESC LIMIT :lim
                    """),
                    {"lim": limit},
                )
            return [dict(r) for r in rows.mappings().all()]

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _row_to_meta(r: Any) -> SecretMetadata:
        policy_raw = (
            r["policy_json"] if isinstance(r["policy_json"], dict) else json.loads(r["policy_json"])
        )
        return SecretMetadata(
            id=r["id"] if isinstance(r["id"], UUID) else UUID(str(r["id"])),
            name=r["name"],
            description=r["description"] or "",
            secret_type=SecretType(r["secret_type"] or "other"),
            policy=SecretPolicy.model_validate(policy_raw),
            usage_count=r["usage_count"] or 0,
            last_used_at=r["last_used_at"],
            created_by=r["created_by"] or "system",
            created_at=r["created_at"],
            updated_at=r["updated_at"],
        )

    async def _audit(
        self,
        name: str,
        requester: str,
        context: str,
        allowed: bool,
        reason: str,
        *,
        strict: bool = False,
    ) -> None:
        try:
            async with self._conn_factory() as conn:
                await conn.execute(
                    sa.text("""
                        INSERT INTO credentials_audit
                            (id, secret_name, requester, context, allowed, reason, resolved_at)
                        VALUES (:id, :name, :req, :ctx, :allowed, :reason, :now)
                    """),
                    {
                        "id": str(uuid4()),
                        "name": name,
                        "req": requester,
                        "ctx": context,
                        "allowed": allowed,
                        "reason": reason,
                        "now": datetime.now(UTC),
                    },
                )
                await conn.commit()
        except Exception:
            logger.exception("credentials.audit_write_failed name=%s", name)
            if strict:
                raise RuntimeError("credential audit persistence is unavailable")

    async def _consume_rate_limit(
        self,
        *,
        name: str,
        requester: str,
        limit: int,
    ) -> bool:
        """Atomically consume one durable per-secret/requester minute slot."""
        window = datetime.now(UTC).replace(second=0, microsecond=0)
        async with self._conn_factory() as conn:
            result = await conn.execute(
                sa.text("""
                    INSERT INTO credential_resolve_rates
                        (secret_name, requester, window_started_at, resolve_count)
                    VALUES (:name, :requester, :window, 1)
                    ON CONFLICT (secret_name, requester, window_started_at)
                    DO UPDATE SET resolve_count = credential_resolve_rates.resolve_count + 1
                    WHERE credential_resolve_rates.resolve_count < :limit
                    RETURNING resolve_count
                """),
                {
                    "name": name,
                    "requester": requester,
                    "window": window,
                    "limit": limit,
                },
            )
            row = result.mappings().first()
            await conn.commit()
        return row is not None

    async def _consume_approval(
        self,
        *,
        approval_id: UUID | str | None,
        name: str,
        requester: str,
        context: str,
    ) -> bool:
        """Consume exactly one matching approval; replays fail closed."""
        if approval_id is None:
            return False
        try:
            parsed_id = approval_id if isinstance(approval_id, UUID) else UUID(str(approval_id))
        except (TypeError, ValueError):
            return False
        async with self._conn_factory() as conn:
            result = await conn.execute(
                sa.text("""
                    UPDATE credential_resolve_approvals
                    SET state = 'CONSUMED', consumed_at = now()
                    WHERE id = :id AND secret_name = :name
                      AND requester = :requester AND context = :context
                      AND state = 'APPROVED' AND expires_at > now()
                    RETURNING id
                """),
                {
                    "id": parsed_id,
                    "name": name,
                    "requester": requester,
                    "context": context,
                },
            )
            row = result.mappings().first()
            await conn.commit()
        return row is not None

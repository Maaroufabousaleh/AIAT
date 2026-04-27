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
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncConnection

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

        await self._audit(name, requester, context, allowed, reason)

        if not allowed:
            logger.warning(
                "credentials.resolve_denied name=%s requester=%s reason=%s",
                name,
                requester,
                reason,
            )
            return None

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

"""Transactional Postgres adapter for a bounded native-span retention batch.

The retention execution contract is intentionally storage-provider neutral.  A
small operational adapter is useful for the local personal instance: it can
prepare a disposable database backup/read-back manifest and apply one
explicitly scoped delete batch in one transaction.  The adapter is not a
general erasure service, does not implement archive storage, and does not
create durable audit records.  Those remain separate authority and recovery
gates.

Only native-span metadata is read.  The adapter never returns row contents to a
caller and the manifest digest is used only to compare the source, backup, and
read-back sets before mutation.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from .retention_execution import (
    RetentionAction,
    RetentionBackupParityEvidence,
    RetentionExecutionError,
    RetentionMutationStore,
)

_NATIVE_TRACE_COLUMNS = (
    "id",
    "trace_id",
    "span_id",
    "parent_span_id",
    "source_kind",
    "operation",
    "service",
    "status",
    "started_at",
    "ended_at",
    "duration_ms",
    "sampled",
    "retention_until",
    "attributes_json",
    "created_at",
)
_NATIVE_TRACE_SELECT = ", ".join(_NATIVE_TRACE_COLUMNS)


def _sync_dsn(value: str) -> str:
    """Convert an async SQLAlchemy DSN into a psycopg2-compatible DSN."""

    rendered = str(value or "").strip()
    if rendered.startswith("postgresql+asyncpg://"):
        rendered = "postgresql://" + rendered.removeprefix("postgresql+asyncpg://")
    elif rendered.startswith("postgresql+psycopg2://"):
        rendered = "postgresql://" + rendered.removeprefix("postgresql+psycopg2://")
    elif rendered.startswith("postgres://"):
        rendered = "postgresql://" + rendered.removeprefix("postgres://")
    if not rendered.startswith("postgresql://"):
        raise RetentionExecutionError("a PostgreSQL DSN is required")
    return rendered


def _canonical_value(value: Any) -> Any:
    """Make driver values deterministic without exposing them in a report."""

    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, Mapping):
        return {
            str(key): _canonical_value(item)
            for key, item in sorted(value.items(), key=lambda item: str(item[0]))
        }
    if isinstance(value, (list, tuple)):
        return [_canonical_value(item) for item in value]
    return value


def _manifest_digest(rows: Sequence[Mapping[str, Any]]) -> str:
    normalized = [
        {
            column: _canonical_value(row.get(column))
            for column in _NATIVE_TRACE_COLUMNS
        }
        for row in sorted(rows, key=lambda row: str(row.get("id")))
    ]
    encoded = json.dumps(
        normalized,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _rows(cursor: Any) -> list[dict[str, Any]]:
    columns = [str(description[0]) for description in cursor.description or ()]
    return [dict(zip(columns, row, strict=True)) for row in cursor.fetchall()]


class PostgresNativeTraceRetentionStore(RetentionMutationStore):
    """Apply one scoped native-span delete batch through Postgres.

    The connection is deliberately created per operation so this adapter can
    be used with a PgBouncer transaction pool.  ``trace_id`` is a hard scope:
    every selected row must belong to it, and archive actions are rejected
    because no archive authority is provided by this local adapter.
    """

    def __init__(
        self,
        dsn: str,
        *,
        trace_id: str,
        max_actions: int = 1_000,
    ) -> None:
        self._dsn = _sync_dsn(dsn)
        self.trace_id = str(trace_id or "").strip()
        if not self.trace_id or len(self.trace_id) > 160:
            raise RetentionExecutionError("a bounded trace scope is required")
        self.max_actions = max(1, min(int(max_actions), 1_000))

    def _connect(self) -> Any:
        try:
            import psycopg2
        except ImportError as exc:  # pragma: no cover - environment dependent
            raise RetentionExecutionError("psycopg2 is unavailable") from exc
        try:
            return psycopg2.connect(self._dsn)
        except Exception as exc:  # noqa: BLE001
            raise RetentionExecutionError("Postgres retention connection failed") from exc

    def _normalized_ids(self, record_ids: Sequence[str]) -> list[str]:
        if not record_ids or len(record_ids) > self.max_actions:
            raise RetentionExecutionError("retention action count is outside the bounded limit")
        normalized: list[str] = []
        for value in record_ids:
            try:
                rendered = str(UUID(str(value).strip()))
            except (TypeError, ValueError, AttributeError) as exc:
                raise RetentionExecutionError("native-span retention IDs must be UUIDs") from exc
            if rendered in normalized:
                raise RetentionExecutionError("duplicate native-span retention ID")
            normalized.append(rendered)
        return normalized

    @staticmethod
    def _assert_rows(
        rows: Sequence[Mapping[str, Any]],
        expected_ids: Sequence[str],
        trace_id: str,
    ) -> None:
        actual_ids = {str(row.get("id")) for row in rows}
        if actual_ids != set(expected_ids) or len(rows) != len(expected_ids):
            raise RetentionExecutionError("native-span backup/read-back set did not match the action set")
        if any(str(row.get("trace_id") or "").strip() != trace_id for row in rows):
            raise RetentionExecutionError("native-span action escaped the trace scope")

    @staticmethod
    def _create_backup_and_restore(cursor: Any, ids: Sequence[str]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """Create disposable database-local backup and clean-target read-back sets."""

        placeholders = "%s::uuid[]"
        cursor.execute(
            "CREATE TEMP TABLE aiat_retention_backup "
            "(LIKE native_trace_spans INCLUDING DEFAULTS) ON COMMIT DROP"
        )
        cursor.execute(
            "INSERT INTO aiat_retention_backup "
            f"SELECT {_NATIVE_TRACE_SELECT} FROM native_trace_spans WHERE id = ANY({placeholders})",
            (list(ids),),
        )
        cursor.execute(
            "SELECT " + _NATIVE_TRACE_SELECT + " FROM aiat_retention_backup ORDER BY id"
        )
        backup_rows = _rows(cursor)
        cursor.execute(
            "CREATE TEMP TABLE aiat_retention_restore "
            "(LIKE native_trace_spans INCLUDING DEFAULTS) ON COMMIT DROP"
        )
        cursor.execute(
            "INSERT INTO aiat_retention_restore "
            f"SELECT {_NATIVE_TRACE_SELECT} FROM aiat_retention_backup"
        )
        cursor.execute(
            "SELECT " + _NATIVE_TRACE_SELECT + " FROM aiat_retention_restore ORDER BY id"
        )
        restored_rows = _rows(cursor)
        return backup_rows, restored_rows

    def prepare_backup_parity(
        self,
        record_ids: Sequence[str],
        *,
        evidence_ref: str,
    ) -> RetentionBackupParityEvidence:
        """Verify a disposable DB-local backup/read-back before apply."""

        ids = self._normalized_ids(record_ids)
        reference = str(evidence_ref or "").strip()
        if not reference or len(reference) > 240:
            raise RetentionExecutionError("backup evidence reference is required")
        connection = self._connect()
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT " + _NATIVE_TRACE_SELECT + " FROM native_trace_spans "
                    "WHERE id = ANY(%s::uuid[]) ORDER BY id FOR UPDATE",
                    (list(ids),),
                )
                source_rows = _rows(cursor)
                self._assert_rows(source_rows, ids, self.trace_id)
                backup_rows, restored_rows = self._create_backup_and_restore(cursor, ids)
                self._assert_rows(backup_rows, ids, self.trace_id)
                self._assert_rows(restored_rows, ids, self.trace_id)
                source_digest = _manifest_digest(source_rows)
                backup_digest = _manifest_digest(backup_rows)
                restored_digest = _manifest_digest(restored_rows)
                if len({source_digest, backup_digest, restored_digest}) != 1:
                    raise RetentionExecutionError("native-span backup/read-back digest mismatch")
            connection.commit()
        except RetentionExecutionError:
            connection.rollback()
            raise
        except Exception as exc:  # noqa: BLE001
            connection.rollback()
            raise RetentionExecutionError("native-span backup/read-back failed") from exc
        finally:
            connection.close()
        return RetentionBackupParityEvidence(
            evidence_ref=reference,
            source_manifest_sha256=source_digest,
            backup_manifest_sha256=backup_digest,
            restored_manifest_sha256=restored_digest,
            source_record_count=len(source_rows),
            backup_record_count=len(backup_rows),
            restored_record_count=len(restored_rows),
            checked_record_count=len(restored_rows),
            clean_target_verified=True,
        )

    def apply_retention_actions(
        self,
        actions: Sequence[RetentionAction],
        *,
        audit: Mapping[str, Any],
    ) -> None:
        """Verify the supplied parity evidence and delete one atomic batch."""

        if not actions:
            return
        if any(action.action != "delete" for action in actions):
            raise RetentionExecutionError("local Postgres retention adapter does not archive records")
        ids = self._normalized_ids([action.record_id for action in actions])
        expected_digest = str(audit.get("backup_manifest_sha256") or "").strip().lower()
        expected_count = audit.get("backup_record_count")
        if len(expected_digest) != 64 or not isinstance(expected_count, int):
            raise RetentionExecutionError("retention audit omitted typed backup parity evidence")
        if audit.get("clean_target_verified") is not True:
            raise RetentionExecutionError("retention audit did not verify a clean backup target")

        connection = self._connect()
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT " + _NATIVE_TRACE_SELECT + " FROM native_trace_spans "
                    "WHERE id = ANY(%s::uuid[]) ORDER BY id FOR UPDATE",
                    (list(ids),),
                )
                source_rows = _rows(cursor)
                self._assert_rows(source_rows, ids, self.trace_id)
                source_digest = _manifest_digest(source_rows)
                if source_digest != expected_digest or len(source_rows) != expected_count:
                    raise RetentionExecutionError("native-span source no longer matches backup parity evidence")
                backup_rows, restored_rows = self._create_backup_and_restore(cursor, ids)
                self._assert_rows(backup_rows, ids, self.trace_id)
                self._assert_rows(restored_rows, ids, self.trace_id)
                if _manifest_digest(backup_rows) != expected_digest or _manifest_digest(restored_rows) != expected_digest:
                    raise RetentionExecutionError("native-span backup/read-back digest mismatch")
                cursor.execute(
                    "DELETE FROM native_trace_spans "
                    "WHERE id = ANY(%s::uuid[]) AND trace_id = %s",
                    (list(ids), self.trace_id),
                )
                if int(cursor.rowcount or 0) != len(ids):
                    raise RetentionExecutionError("native-span delete count did not match the action set")
            connection.commit()
        except RetentionExecutionError:
            connection.rollback()
            raise
        except Exception as exc:  # noqa: BLE001
            connection.rollback()
            raise RetentionExecutionError("native-span retention transaction failed") from exc
        finally:
            connection.close()


__all__ = [
    "PostgresNativeTraceRetentionStore",
]

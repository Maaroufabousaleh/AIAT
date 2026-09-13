-- AIAT D1 raw-message storage.
--
-- D1 documents a 2,000,000-byte maximum for a string, BLOB, or table row.
-- The Worker deliberately uses 256 KiB chunks, leaving substantial room for
-- row/binding overhead and keeping the default 4 MiB message bound to at most
-- 16 chunks. The published limit is provider-controlled; see the Worker
-- README for the source and the operational caveat.

PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS mail_messages (
  message_id TEXT PRIMARY KEY,
  event_id TEXT UNIQUE,
  provider_key TEXT NOT NULL UNIQUE,
  provider_message_id TEXT,
  rfc_message_id TEXT,
  identity_id TEXT NOT NULL,
  worker_id TEXT NOT NULL,
  envelope_recipient TEXT NOT NULL,
  sender TEXT,
  subject TEXT NOT NULL DEFAULT '',
  received_at TEXT NOT NULL,
  raw_object_key TEXT NOT NULL,
  raw_size INTEGER NOT NULL DEFAULT 0 CHECK (raw_size >= 0),
  content_hash TEXT NOT NULL DEFAULT '',
  storage_backend TEXT NOT NULL CHECK (storage_backend IN ('d1', 'r2')),
  storage_state TEXT NOT NULL CHECK (storage_state IN (
    'RECEIVING', 'STORED', 'EVENT_READY', 'ACKNOWLEDGED',
    'REJECTED', 'REJECTED_OVERSIZE', 'DELETED'
  )),
  chunk_count INTEGER NOT NULL DEFAULT 0 CHECK (chunk_count >= 0),
  processed_at TEXT,
  protected_until TEXT,
  deleted_at TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_mail_messages_identity_received
  ON mail_messages(identity_id, received_at DESC);

CREATE INDEX IF NOT EXISTS ix_mail_messages_storage_state_updated
  ON mail_messages(storage_state, updated_at);

CREATE INDEX IF NOT EXISTS ix_mail_messages_retention
  ON mail_messages(deleted_at, protected_until, processed_at, received_at);

CREATE TABLE IF NOT EXISTS mail_message_chunks (
  message_id TEXT NOT NULL REFERENCES mail_messages(message_id) ON DELETE CASCADE,
  chunk_index INTEGER NOT NULL CHECK (chunk_index >= 0),
  chunk_data BLOB NOT NULL,
  chunk_size_bytes INTEGER NOT NULL CHECK (chunk_size_bytes BETWEEN 1 AND 262144),
  chunk_sha256 TEXT NOT NULL,
  created_at TEXT NOT NULL,
  PRIMARY KEY (message_id, chunk_index)
);

CREATE INDEX IF NOT EXISTS ix_mail_message_chunks_message_order
  ON mail_message_chunks(message_id, chunk_index);

-- Preserve any rows created by the original R2-backed implementation. They
-- remain readable only through the optional R2 profile; new rows never depend
-- on that legacy table or binding.
INSERT OR IGNORE INTO mail_messages (
  message_id, event_id, provider_key, provider_message_id, rfc_message_id,
  identity_id, worker_id, envelope_recipient, sender, subject, received_at,
  raw_object_key, raw_size, content_hash, storage_backend, storage_state,
  chunk_count, processed_at, protected_until, deleted_at, created_at, updated_at
)
SELECT
  message.message_id,
  message.event_id,
  message.provider_key,
  event.provider_message_id,
  event.provider_message_id,
  message.identity_id,
  message.worker_id,
  message.envelope_recipient,
  message.sender,
  message.subject,
  message.received_at,
  message.raw_object_key,
  message.raw_size,
  message.content_hash,
  'r2',
  CASE WHEN message.deleted_at IS NULL THEN 'EVENT_READY' ELSE 'DELETED' END,
  0,
  message.processed_at,
  message.protected_until,
  message.deleted_at,
  message.received_at,
  COALESCE(message.deleted_at, message.received_at)
FROM inbound_messages AS message
LEFT JOIN inbound_events AS event ON event.event_id = message.event_id;

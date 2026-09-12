-- AIAT Cloudflare Email Worker metadata schema.
-- Raw MIME is stored only in the bound R2 bucket. D1 contains bounded
-- metadata, registry state, event ordering, and retention markers.

CREATE TABLE IF NOT EXISTS recipient_registry (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  identity_id TEXT NOT NULL,
  worker_id TEXT NOT NULL,
  address TEXT NOT NULL UNIQUE,
  provider_reference TEXT NOT NULL,
  idempotency_key TEXT NOT NULL UNIQUE,
  state TEXT NOT NULL CHECK (state IN ('VERIFYING', 'ACTIVE', 'SUSPENDED', 'RETIRED')),
  created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
  updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE INDEX IF NOT EXISTS ix_recipient_registry_identity
  ON recipient_registry(identity_id);

CREATE INDEX IF NOT EXISTS ix_recipient_registry_provider_reference
  ON recipient_registry(provider_reference);

CREATE TABLE IF NOT EXISTS inbound_events (
  sequence INTEGER PRIMARY KEY AUTOINCREMENT,
  event_id TEXT NOT NULL UNIQUE,
  message_id TEXT NOT NULL UNIQUE,
  provider_message_id TEXT,
  provider_key TEXT NOT NULL UNIQUE,
  identity_id TEXT NOT NULL,
  worker_id TEXT NOT NULL,
  envelope_recipient TEXT NOT NULL,
  event_type TEXT NOT NULL,
  received_at TEXT NOT NULL,
  raw_object_key TEXT NOT NULL,
  raw_size INTEGER NOT NULL,
  content_hash TEXT NOT NULL,
  acked_at TEXT
);

CREATE INDEX IF NOT EXISTS ix_inbound_events_sequence
  ON inbound_events(sequence);

CREATE TABLE IF NOT EXISTS inbound_messages (
  message_id TEXT PRIMARY KEY,
  event_id TEXT NOT NULL UNIQUE REFERENCES inbound_events(event_id),
  provider_key TEXT NOT NULL UNIQUE,
  identity_id TEXT NOT NULL,
  worker_id TEXT NOT NULL,
  envelope_recipient TEXT NOT NULL,
  sender TEXT,
  subject TEXT NOT NULL DEFAULT '',
  received_at TEXT NOT NULL,
  raw_object_key TEXT NOT NULL,
  raw_size INTEGER NOT NULL,
  content_hash TEXT NOT NULL,
  processed_at TEXT,
  protected_until TEXT,
  deleted_at TEXT
);

CREATE INDEX IF NOT EXISTS ix_inbound_messages_identity_received
  ON inbound_messages(identity_id, received_at DESC);

CREATE TABLE IF NOT EXISTS edge_nonces (
  nonce TEXT PRIMARY KEY,
  expires_at INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_edge_nonces_expiry
  ON edge_nonces(expires_at);

"""
memory — Persistent storage helpers.

Exports (Phase 7)
-----------------
AgentStorage    Async Postgres wrapper (asyncpg + SQLAlchemy core).
                All queries filter by agent_id automatically.
                Connection string points to PgBouncer.
BlobClient      Thin async MinIO/S3 wrapper (aioboto3).
                upload(project_id, path, data) → BlobRef
                download(blob_ref) → bytes
                Enforces agent_id prefix on all keys.
CheckpointStore Thin helpers: save_checkpoint / load_checkpoint / delete_checkpoint
                backed by the agent_checkpoints Postgres table.
"""

# Populated in Phase 7.

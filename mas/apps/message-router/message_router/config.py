"""Message-router configuration — loaded once at startup from environment variables."""

from __future__ import annotations

import logging
import os

from pydantic import AliasChoices, Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


_DEFAULT_POSTGRES_PASSWORD = "mas"
_DEFAULT_AGENT_TOKEN_SECRET = "changeme"

logger = logging.getLogger(__name__)


class Settings(BaseSettings):
    """All configuration for the message-router service.

    Loaded from environment variables (case-insensitive).  Sensitive values
    (``REDIS_URL``, ``POSTGRES_DSN``) are provided by the Docker Compose
    environment; agents never receive these.
    """

    model_config = SettingsConfigDict(env_prefix="", case_sensitive=False)

    # ── Redis ──────────────────────────────────────────────────────────────────
    redis_url: str = Field(
        default="redis://localhost:6379",
        description="Redis connection URL.  Only the router and tool-service know this.",
    )
    redis_username: str | None = Field(
        default=None,
        description="Redis ACL username for the router_user (see redis.conf ACL setup).",
    )
    redis_password: str | None = Field(
        default=None,
        description="Redis ACL password for the router_user.",
    )

    # ── Postgres (DLQ writes) ──────────────────────────────────────────────────
    postgres_dsn: str = Field(
        default="postgresql://mas:mas@localhost:5432/mas",
        description=(
            "Postgres connection string for dead-letter writes.  Points at PgBouncer in production."
        ),
    )

    # ── Agent auth ────────────────────────────────────────────────────────────
    agent_token_secret: str = Field(
        default="changeme",
        validation_alias=AliasChoices("AGENT_TOKEN_SECRET", "ROUTER_SECRET"),
        description=(
            "Shared secret used to authenticate agent WebSocket connections.  "
            "Each agent presents 'Bearer <agent_id>:<secret>' in the Authorization header."
        ),
    )

    # ── Streams ───────────────────────────────────────────────────────────────
    stream_prefix: str = Field(default="stream", description="Key prefix for team streams.")
    group_prefix: str = Field(default="group", description="Consumer group name prefix.")
    dedupe_prefix: str = Field(default="dedupe", description="Key prefix for publish dedupe keys.")

    dedupe_ttl_seconds: int = Field(
        default=300, description="Publish-side idempotency TTL in seconds."
    )
    stream_max_len: int = Field(
        default=50_000, description="Approximate MAXLEN for each team stream (XTRIM)."
    )
    trim_interval_seconds: int = Field(
        default=60, description="How often (seconds) to run the stream trim background task."
    )

    # ── XAUTOCLAIM reclaim ────────────────────────────────────────────────────
    reclaim_idle_ms: int = Field(
        default=120_000, description="Messages idle longer than this (ms) are reclaimed."
    )
    reclaim_interval_seconds: int = Field(
        default=30, description="How often (seconds) to run the XAUTOCLAIM reclaim background task."
    )
    max_delivery_attempts: int = Field(
        default=3,
        description=(
            "After this many failed delivery attempts the message is moved to the DLQ "
            "(Postgres dead_letters table)."
        ),
    )

    # ── WS keepalive ──────────────────────────────────────────────────────────
    ws_ping_interval_seconds: int = Field(
        default=15, description="How often (seconds) the router sends PING frames to agents."
    )
    ws_pong_timeout_seconds: int = Field(
        default=10,
        description=("Seconds the router waits for a PONG before considering the connection dead."),
    )

    # ── XREADGROUP batch ──────────────────────────────────────────────────────
    read_count: int = Field(
        default=10, description="How many messages to fetch per XREADGROUP call."
    )
    read_block_ms: int = Field(
        default=5_000, description="Block timeout (ms) for XREADGROUP when no messages arrive."
    )

    # ── Known teams ───────────────────────────────────────────────────────────
    known_teams: list[str] = Field(
        default=[
            "exec_ceo",
            "exec_coo",
            "office_cfo",
            "office_cio",
            "office_chrm",
            "office_cso",
            "office_cto",
            "dept_production",
            "dept_system",
            "dept_qa",
            "dept_devops",
        ],
        description="All 11 team IDs.  Consumer groups are pre-created for these on startup.",
    )

    # ── Orchestrator stream ────────────────────────────────────────────────────
    orchestrator_stream: str = Field(
        default="stream:exec_ceo",
        description="Stream used for SHUTDOWN_ACK and DLQ SYSTEM_EVENT notifications.",
    )

    @model_validator(mode="after")
    def _warn_default_credentials(self) -> "Settings":
        if os.environ.get("MAS_ENVIRONMENT") == "production":
            if self.postgres_dsn and _DEFAULT_POSTGRES_PASSWORD in self.postgres_dsn:
                raise ValueError(
                    "POSTGRES_DSN must not use the default password in production. "
                    "Set the POSTGRES_PASSWORD environment variable."
                )
            if self.agent_token_secret == _DEFAULT_AGENT_TOKEN_SECRET:
                raise ValueError(
                    "AGENT_TOKEN_SECRET must not use the default value in production. "
                    "Set the AGENT_TOKEN_SECRET environment variable."
                )
        elif "mas:mas@localhost" in self.postgres_dsn:
            logger.warning("Using default Postgres password. Set POSTGRES_PASSWORD to override.")
        elif self.agent_token_secret == _DEFAULT_AGENT_TOKEN_SECRET:
            logger.warning("Using default agent token secret. Set AGENT_TOKEN_SECRET to override.")
        return self


# Module-level singleton — import this everywhere in the router.
settings = Settings()

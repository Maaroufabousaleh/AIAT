"""Settings for the tool-service — loaded from environment variables."""

from __future__ import annotations

import base64
import json
import logging
import os
from functools import lru_cache

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings

logger = logging.getLogger(__name__)

_DEFAULT_REDIS_PASSWORD = "toolcache_default_pass"
_DEFAULT_MINIO_SECRET = "change_me"


class Settings(BaseSettings):
    """Tool-service configuration.

    All fields are populated from environment variables.
    """

    redis_url: str = "redis://localhost:6379/1"
    redis_username: str = "toolcache_user"
    redis_password: str = "toolcache_default_pass"
    redis_db_shared_memory: int = 2

    tool_secret: str = ""
    pgbouncer_dsn: str | None = None
    aiat_tool_caller_public_keys_json: str = "{}"
    aiat_tool_delegate_client_ids_json: str = "[]"

    llm_gateway_url: str = "http://llm-gateway:8003"
    llm_api_key: str = ""

    log_level: str = "INFO"

    cb_failure_threshold: int = 3
    cb_failure_window_seconds: int = 60
    cb_open_duration_seconds: int = 120

    orchestrator_url: str = "http://orchestrator-api:8000"

    minio_endpoint: str = "http://minio:9000"
    minio_access_key: str = "mas_agent"
    minio_secret_key: str = "change_me"
    minio_bucket: str = "mas-agents"

    host: str = "0.0.0.0"
    port: int = 8002

    http_transport_endpoints: dict[str, str] = Field(default_factory=dict)
    mcp_transport_endpoints: dict[str, str] = Field(default_factory=dict)
    aiat_mcp_servers_json: str = "{}"
    process_transport_commands: dict[str, list[str]] = Field(default_factory=dict)
    transport_request_timeout_seconds: float = 15.0

    @property
    def mcp_servers(self) -> dict[str, dict]:
        """Return the validated operator-configured MCP server registry."""
        try:
            value = json.loads(self.aiat_mcp_servers_json)
        except json.JSONDecodeError as exc:
            raise ValueError("AIAT_MCP_SERVERS_JSON must be valid JSON") from exc
        if not isinstance(value, dict):
            raise ValueError("AIAT_MCP_SERVERS_JSON must contain a JSON object")
        return {str(name): dict(config) for name, config in value.items() if isinstance(config, dict)}

    @property
    def environment_is_production(self) -> bool:
        return os.getenv("MAS_ENVIRONMENT", "development").strip().lower() in {"production", "prod", "staging"}

    @property
    def tool_caller_public_keys(self) -> dict[str, str]:
        try:
            value = json.loads(self.aiat_tool_caller_public_keys_json)
        except json.JSONDecodeError as exc:
            raise ValueError("AIAT_TOOL_CALLER_PUBLIC_KEYS_JSON must be valid JSON") from exc
        if not isinstance(value, dict) or not all(
            isinstance(key, str) and isinstance(item, str) for key, item in value.items()
        ):
            raise ValueError("AIAT_TOOL_CALLER_PUBLIC_KEYS_JSON must map client ids to public keys")
        return value

    @property
    def tool_delegate_client_ids(self) -> frozenset[str]:
        try:
            value = json.loads(self.aiat_tool_delegate_client_ids_json)
        except json.JSONDecodeError as exc:
            raise ValueError("AIAT_TOOL_DELEGATE_CLIENT_IDS_JSON must be valid JSON") from exc
        if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
            raise ValueError("AIAT_TOOL_DELEGATE_CLIENT_IDS_JSON must be a string list")
        return frozenset(value)

    model_config = {"env_prefix": "", "case_sensitive": False}

    @model_validator(mode="after")
    def _warn_default_credentials(self) -> Settings:
        if self.environment_is_production:
            if self.redis_password == _DEFAULT_REDIS_PASSWORD:
                raise ValueError(
                    "MAS_REDIS_PASSWORD must not use the default value in production. "
                    "Set the REDIS_PASSWORD environment variable."
                )
            if self.minio_secret_key == _DEFAULT_MINIO_SECRET:
                raise ValueError(
                    "MAS_MINIO_SECRET_KEY must not use the default value in production. "
                    "Set the MINIO_SECRET_KEY environment variable."
                )
            if not self.pgbouncer_dsn:
                raise ValueError("PGBOUNCER_DSN is required for durable production tool grants")
            if not self.tool_secret or len(self.tool_secret) < 32 or "change_me" in self.tool_secret.lower():
                raise ValueError("TOOL_SECRET must be a non-placeholder secret of at least 32 characters in production")
            if not self.tool_caller_public_keys:
                raise ValueError("AIAT_TOOL_CALLER_PUBLIC_KEYS_JSON is required in production")
            for client_id, encoded_key in self.tool_caller_public_keys.items():
                try:
                    raw_key = base64.b64decode(encoded_key, validate=True)
                except Exception as exc:
                    raise ValueError(f"tool caller public key is malformed for {client_id}") from exc
                if len(raw_key) != 32:
                    raise ValueError(f"tool caller public key must be Ed25519 raw bytes for {client_id}")
            unknown_delegates = self.tool_delegate_client_ids - set(self.tool_caller_public_keys)
            if unknown_delegates:
                raise ValueError("tool delegate clients must have registered public keys")
        elif self.redis_password == _DEFAULT_REDIS_PASSWORD:
            logger.warning("Using default Redis password. Set REDIS_PASSWORD to override.")
        elif self.minio_secret_key == _DEFAULT_MINIO_SECRET:
            logger.warning("Using default MinIO secret key. Set MINIO_SECRET_KEY to override.")
        return self


@lru_cache
def get_settings() -> Settings:
    """Return a cached Settings instance."""
    return Settings()

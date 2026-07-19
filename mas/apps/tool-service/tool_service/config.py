"""Settings for the tool-service — loaded from environment variables."""

from __future__ import annotations

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

    model_config = {"env_prefix": "", "case_sensitive": False}

    @model_validator(mode="after")
    def _warn_default_credentials(self) -> Settings:
        if os.environ.get("MAS_ENVIRONMENT") == "production":
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
        elif self.redis_password == _DEFAULT_REDIS_PASSWORD:
            logger.warning("Using default Redis password. Set REDIS_PASSWORD to override.")
        elif self.minio_secret_key == _DEFAULT_MINIO_SECRET:
            logger.warning("Using default MinIO secret key. Set MINIO_SECRET_KEY to override.")
        return self


@lru_cache
def get_settings() -> Settings:
    """Return a cached Settings instance."""
    return Settings()

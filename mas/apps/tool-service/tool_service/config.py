"""Settings for the tool-service — loaded from environment variables."""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Tool-service configuration.

    All fields are populated from environment variables.
    """

    redis_url: str = "redis://localhost:6379/1"
    redis_username: str = "toolcache_user"
    redis_password: str = "toolcache_default_pass"

    tool_secret: str = ""

    llm_gateway_url: str = "http://llm-gateway:8003"
    llm_api_key: str = ""

    log_level: str = "INFO"

    cb_failure_threshold: int = 3
    cb_failure_window_seconds: int = 60
    cb_open_duration_seconds: int = 120

    orchestrator_url: str = "http://orchestrator-api:8000"

    host: str = "0.0.0.0"
    port: int = 8002

    http_transport_endpoints: dict[str, str] = Field(default_factory=dict)
    mcp_transport_endpoints: dict[str, str] = Field(default_factory=dict)
    process_transport_commands: dict[str, list[str]] = Field(default_factory=dict)
    transport_request_timeout_seconds: float = 15.0

    model_config = {"env_prefix": "", "case_sensitive": False}


@lru_cache
def get_settings() -> Settings:
    """Return a cached Settings instance."""
    return Settings()

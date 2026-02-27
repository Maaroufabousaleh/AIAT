"""Settings for the tool-service — loaded from environment variables.

Env vars (set by docker-compose)
---------------------------------
REDIS_URL            redis://:pass@redis:6379/1
REDIS_USERNAME       toolcache_user
REDIS_PASSWORD       toolcache_default_pass
TOOL_SECRET          shared bearer-token secret
LLM_GATEWAY_URL      URL of the LLM gateway
LLM_API_KEY          API key for LLM provider
LOG_LEVEL            INFO
"""

from __future__ import annotations

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Tool-service configuration.

    All fields are populated from environment variables.
    """

    # Redis
    redis_url: str = "redis://localhost:6379/1"
    redis_username: str = "toolcache_user"
    redis_password: str = "toolcache_default_pass"

    # Auth
    tool_secret: str = ""

    # LLM gateway (used by web_search, etc.)
    llm_gateway_url: str = "http://llm-gateway:8003"
    llm_api_key: str = ""

    # Logging
    log_level: str = "INFO"

    # Circuit breaker defaults
    cb_failure_threshold: int = 3
    cb_failure_window_seconds: int = 60
    cb_open_duration_seconds: int = 120

    # Service
    host: str = "0.0.0.0"
    port: int = 8002

    model_config = {"env_prefix": "", "case_sensitive": False}


def get_settings() -> Settings:
    """Return a cached Settings instance."""
    return Settings()

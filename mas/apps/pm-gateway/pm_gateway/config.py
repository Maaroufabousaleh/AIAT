"""Environment-backed settings for the provider gateway."""

from __future__ import annotations

from pydantic import AliasChoices, Field, model_validator
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    orchestrator_url: str = Field(
        default="http://orchestrator-api:8000",
        validation_alias=AliasChoices("ORCHESTRATOR_URL", "PM_GATEWAY_ORCHESTRATOR_URL"),
    )
    pm_gateway_api_key: str = Field(
        default="", validation_alias=AliasChoices("PM_GATEWAY_API_KEY", "GATEWAY_API_KEY")
    )
    host: str = Field(default="0.0.0.0", validation_alias=AliasChoices("HOST", "PM_GATEWAY_HOST"))
    port: int = Field(default=8010, validation_alias=AliasChoices("PORT", "PM_GATEWAY_PORT"))
    outbox_interval_seconds: float = Field(
        default=5.0,
        ge=0.5,
        le=300.0,
        validation_alias=AliasChoices("OUTBOX_INTERVAL_SECONDS", "PM_GATEWAY_OUTBOX_INTERVAL_SECONDS"),
    )
    outbox_batch_size: int = Field(
        default=100,
        ge=1,
        le=1000,
        validation_alias=AliasChoices("OUTBOX_BATCH_SIZE", "PM_GATEWAY_OUTBOX_BATCH_SIZE"),
    )
    webhook_body_max_bytes: int = Field(
        default=1 * 1024 * 1024,
        ge=1024,
        le=10 * 1024 * 1024,
        validation_alias=AliasChoices("PM_GATEWAY_WEBHOOK_BODY_MAX_BYTES", "WEBHOOK_BODY_MAX_BYTES"),
    )
    environment: str = Field(
        default="development",
        # Prefer the gateway-specific profile when a shared process also
        # exports a generic ENVIRONMENT/MAS_ENVIRONMENT value.
        validation_alias=AliasChoices("PM_GATEWAY_ENVIRONMENT", "ENVIRONMENT", "MAS_ENVIRONMENT"),
    )

    model_config = {"env_prefix": "", "case_sensitive": False, "populate_by_name": True}

    @model_validator(mode="after")
    def validate_production(self) -> Settings:
        if self.environment.lower() in {"production", "prod", "staging"} and len(self.pm_gateway_api_key) < 32:
            raise ValueError("PM_GATEWAY_API_KEY must be configured for the PM gateway")
        if "@" in self.orchestrator_url or "?" in self.orchestrator_url:
            raise ValueError("ORCHESTRATOR_URL must not contain credentials or query parameters")
        return self

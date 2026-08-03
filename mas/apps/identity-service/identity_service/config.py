"""Fail-closed configuration for the mail-edge identity service."""

from __future__ import annotations

import base64
import json
from functools import lru_cache

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings
from sqlalchemy import URL


class IdentitySettings(BaseSettings):
    """Runtime settings.

    Secrets are references to environment injection only.  The values are
    neither persisted nor included in API responses/log records.
    """

    environment: str = Field(default="development", alias="MAS_ENVIRONMENT")
    # Compose profiles set this explicitly.  Leaving it empty keeps library
    # and unit-test construction backwards compatible while production/local
    # deployments remain unambiguous at their boundary.
    identity_profile: str = Field(default="", alias="IDENTITY_PROFILE")
    identity_database_dsn: str | None = None
    identity_database_host: str = "identity-postgres"
    identity_database_port: int = 5432
    identity_database_name: str = "identity"
    identity_database_user: str = "identity_service"
    identity_database_password: str = ""
    identity_service_secret: str = ""
    identity_content_encryption_key: str = ""
    identity_client_public_keys_json: str = "{}"
    identity_client_scopes_json: str = "{}"
    identity_bootstrap_token: str = ""
    identity_service_url: str = "http://identity-service:8010"
    public_identity_url: str = "https://identity.aiat.ca"
    primary_domain: str = "aiat.ca"
    agent_mail_domain: str = "agents.aiat.ca"
    mail_hostname: str = "mail.aiat.ca"
    stalwart_public_url: str = "https://mail.aiat.ca"
    stalwart_api_key: str = ""
    stalwart_jmap_service_token: str = ""
    outbound_relay_provider: str = "resend"
    outbound_relay_host: str = "smtp.resend.com"
    outbound_relay_port: int = 465
    outbound_relay_tls_mode: str = "implicit"
    resend_api_key: str = ""
    # This is an activation latch, not a provider-health guess.  It may be
    # enabled only after the operator records the live Resend certification.
    outbound_relay_certified: bool = False
    direct_mx_outbound_enabled: bool = False
    default_mailbox_quota_mb: int = 100
    default_mail_retention_days: int = 180
    default_outbound_enabled: bool = False
    browser_runtime_location: str = "operator_laptop"
    request_timeout_seconds: float = 15.0
    provider_rate_limit_per_minute: int = Field(default=120, ge=1, le=10_000)
    outbound_rate_limit_per_minute: int = Field(default=30, ge=1, le=1_000)
    host: str = "0.0.0.0"
    port: int = 8010

    model_config = {"env_prefix": "", "case_sensitive": False}

    @property
    def is_production(self) -> bool:
        return self.environment.lower() in {"production", "prod", "staging"}

    @property
    def database_dsn(self) -> str | None:
        if self.identity_database_dsn:
            return self.identity_database_dsn
        if not self.identity_database_password:
            return None
        return URL.create(
            "postgresql+asyncpg",
            username=self.identity_database_user,
            password=self.identity_database_password,
            host=self.identity_database_host,
            port=self.identity_database_port,
            database=self.identity_database_name,
        ).render_as_string(hide_password=False)

    @property
    def client_public_keys(self) -> dict[str, str]:
        try:
            value = json.loads(self.identity_client_public_keys_json)
        except json.JSONDecodeError as exc:
            raise ValueError("IDENTITY_CLIENT_PUBLIC_KEYS_JSON must be valid JSON") from exc
        if not isinstance(value, dict) or not all(
            isinstance(k, str) and isinstance(v, str) for k, v in value.items()
        ):
            raise ValueError("IDENTITY_CLIENT_PUBLIC_KEYS_JSON must map client ids to public keys")
        return value

    @property
    def client_scopes(self) -> dict[str, frozenset[str]]:
        try:
            value = json.loads(self.identity_client_scopes_json)
        except json.JSONDecodeError as exc:
            raise ValueError("IDENTITY_CLIENT_SCOPES_JSON must be valid JSON") from exc
        if not isinstance(value, dict):
            raise ValueError("IDENTITY_CLIENT_SCOPES_JSON must contain a JSON object")
        result: dict[str, frozenset[str]] = {}
        for client_id, scopes in value.items():
            if not isinstance(client_id, str) or not isinstance(scopes, list) or not all(isinstance(scope, str) for scope in scopes):
                raise ValueError("IDENTITY_CLIENT_SCOPES_JSON must map client ids to string lists")
            result[client_id] = frozenset(scopes)
        return result

    @field_validator("agent_mail_domain", "mail_hostname", "primary_domain")
    @classmethod
    def _require_dns_name(cls, value: str) -> str:
        value = value.strip().lower().rstrip(".")
        if not value or "." not in value or any(c.isspace() for c in value):
            raise ValueError("mail and identity domains must be DNS names")
        return value

    @model_validator(mode="after")
    def _fail_closed_production(self) -> IdentitySettings:
        profile = self.identity_profile.strip().lower()
        if profile not in {"", "development", "production"}:
            raise ValueError("IDENTITY_PROFILE must be development or production")
        if profile == "production":
            if not self.is_production:
                raise ValueError("production identity profile requires MAS_ENVIRONMENT=production")
            if self.agent_mail_domain != "agents.aiat.ca" or self.mail_hostname != "mail.aiat.ca":
                raise ValueError("production identity profile requires agents.aiat.ca and mail.aiat.ca")
        elif profile == "development":
            if self.is_production:
                raise ValueError("development identity profile cannot run with MAS_ENVIRONMENT=production")
            if self.agent_mail_domain != "agents.aiat.local":
                raise ValueError("development identity profile requires agents.aiat.local")
        if self.direct_mx_outbound_enabled:
            raise ValueError("DIRECT_MX_OUTBOUND_ENABLED must remain false")
        if self.default_outbound_enabled:
            raise ValueError("DEFAULT_OUTBOUND_ENABLED must remain false")
        relay_provider = self.outbound_relay_provider.strip().lower()
        relay_disabled = relay_provider in {"disabled", "none", "off"}
        if relay_disabled:
            if self.outbound_relay_certified:
                raise ValueError("OUTBOUND_RELAY_CERTIFIED requires the Resend relay")
            if self.is_production:
                raise ValueError("production identity service requires the approved Resend relay")
        else:
            if relay_provider != "resend":
                raise ValueError("Resend is the only approved outbound relay")
            if self.outbound_relay_port not in {465, 587}:
                raise ValueError("Resend relay must use authenticated TLS port 465 or 587")
            expected_tls_mode = "implicit" if self.outbound_relay_port == 465 else "starttls"
            if self.outbound_relay_tls_mode.strip().lower() != expected_tls_mode:
                raise ValueError(
                    f"Resend port {self.outbound_relay_port} requires {expected_tls_mode} TLS mode"
                )
        if self.is_production:
            required = {
                "IDENTITY_DATABASE_PASSWORD": self.identity_database_password or self.identity_database_dsn,
                "IDENTITY_SERVICE_SECRET": self.identity_service_secret,
                "IDENTITY_CONTENT_ENCRYPTION_KEY": self.identity_content_encryption_key,
                "STALWART_API_KEY": self.stalwart_api_key,
                "STALWART_JMAP_SERVICE_TOKEN": self.stalwart_jmap_service_token,
                "RESEND_API_KEY": self.resend_api_key,
            }
            missing = [name for name, value in required.items() if not value]
            if missing:
                raise ValueError("missing required production identity configuration: " + ", ".join(missing))
            placeholder_values = {
                name
                for name, value in required.items()
                if value and ("change_me" in str(value).lower() or "placeholder" in str(value).lower())
            }
            if placeholder_values:
                raise ValueError(
                    "production identity configuration contains placeholder values: "
                    + ", ".join(sorted(placeholder_values))
                )
            if len(self.identity_service_secret) < 32:
                raise ValueError("IDENTITY_SERVICE_SECRET must contain at least 32 characters")
            try:
                encryption_key = base64.urlsafe_b64decode(
                    self.identity_content_encryption_key.encode()
                )
            except Exception as exc:
                raise ValueError(
                    "IDENTITY_CONTENT_ENCRYPTION_KEY must be a Fernet-compatible key"
                ) from exc
            if len(encryption_key) != 32:
                raise ValueError(
                    "IDENTITY_CONTENT_ENCRYPTION_KEY must decode to 32 bytes"
                )
            for name, value in {
                "STALWART_API_KEY": self.stalwart_api_key,
                "STALWART_JMAP_SERVICE_TOKEN": self.stalwart_jmap_service_token,
                "RESEND_API_KEY": self.resend_api_key,
            }.items():
                if len(value) < 20:
                    raise ValueError(f"{name} is too short for production")
            if not self.client_public_keys:
                raise ValueError("IDENTITY_CLIENT_PUBLIC_KEYS_JSON must register signed production clients")
            for client_id, public_key in self.client_public_keys.items():
                try:
                    decoded = base64.b64decode(public_key, validate=True)
                except Exception as exc:
                    raise ValueError(
                        f"identity client public key is malformed for {client_id}"
                    ) from exc
                if len(decoded) != 32:
                    raise ValueError(
                        f"identity client public key must be Ed25519 raw bytes for {client_id}"
                    )
            unknown_scope_clients = set(self.client_scopes) - set(self.client_public_keys)
            if unknown_scope_clients:
                raise ValueError("identity client scopes reference unregistered clients")
            if self.browser_runtime_location != "operator_laptop":
                raise ValueError("BROWSER_RUNTIME_LOCATION must remain operator_laptop")
        return self


@lru_cache
def get_settings() -> IdentitySettings:
    return IdentitySettings()


def decode_secret_bytes(value: str, *, name: str) -> bytes:
    """Decode a base64 secret without echoing its value in errors."""
    try:
        return base64.b64decode(value, validate=True)
    except Exception as exc:
        raise ValueError(f"{name} must be base64 encoded") from exc

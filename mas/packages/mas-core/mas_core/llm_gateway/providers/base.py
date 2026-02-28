"""Base classes for the provider registry.

This module contains the core types shared by all provider implementations:
``ApiStyle``, ``ProviderConfig``, ``ModelEntry``, and ``ModelRegistry``.
"""

from __future__ import annotations

import os
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# API style enum
# ---------------------------------------------------------------------------


class ApiStyle(str, Enum):
    """Wire-level protocol used to talk to the LLM backend.

    CHAT_COMPLETIONS
        OpenAI ``/v1/chat/completions`` format.
        Request:  ``{ model, messages: [{role, content}], … }``
        Response: ``{ choices: [{message: {role, content, tool_calls}}], usage }``

    RESPONSES
        OpenAI Responses API (``/v1/responses`` or provider variant).
        Request:  ``{ model, input: [{role, content: [{type, text}]}], … }``
        Response: ``{ output_text | output: [{content: [{text}]}] }``

    CLI
        Spawn a local subprocess, pipe prompt via stdin, read stdout.
        Used for llama.cpp, Ollama CLI mode, or any local binary.
    """

    CHAT_COMPLETIONS = "chat_completions"
    RESPONSES = "responses"
    CLI = "cli"


# ---------------------------------------------------------------------------
# Provider config
# ---------------------------------------------------------------------------


class ProviderConfig(BaseModel):
    """Shared settings for all models belonging to one provider.

    Providers are registered once; individual ``ModelEntry`` objects reference
    the provider by name.  At request time the client merges provider-level
    headers / auth with model-level overrides.
    """

    provider_id: str = Field(..., description="Unique provider key, e.g. 'openai', 'zen'.")
    base_url: str = Field(
        ...,
        description="Default base URL. ModelEntry.endpoint can override per-model.",
    )
    api_key_env_vars: list[str] = Field(
        default_factory=list,
        description=(
            "Ordered list of env-var names to probe for the API key. "
            "First non-empty value wins.  If none are set, falls back to "
            "'public' (useful for keyless / free providers)."
        ),
    )
    default_api_key: str = Field(
        default="",
        description="Hardcoded fallback key when no env var is set.",
    )
    extra_headers: dict[str, str] = Field(
        default_factory=dict,
        description="Extra HTTP headers sent with every request to this provider.",
    )
    description: str = Field(default="", description="Human-readable provider description.")

    def resolve_api_key(self) -> str:
        """Return the first non-empty key from ``api_key_env_vars``, or the default."""
        for var in self.api_key_env_vars:
            val = os.environ.get(var, "")
            if val:
                return val
        return self.default_api_key or "public"


# ---------------------------------------------------------------------------
# Model capabilities
# ---------------------------------------------------------------------------


class ModelCapabilities(BaseModel):
    """Declares what input modalities and features a model actually supports.

    These flags represent verified behaviour — not theoretical API support.
    A model behind a proxy that strips images should set ``supports_images``
    to ``False`` even if the underlying model family is vision-capable.
    """

    supports_images: bool = Field(
        default=False,
        description=(
            "Model can process image inputs (base64 data-URL or image_url). "
            "True only if the provider endpoint actually passes images through."
        ),
    )
    supports_pdf: bool = Field(
        default=False,
        description=(
            "Model can ingest PDF files directly (base64 / file-id / URL). "
            "Requires Responses API *and* provider-side support."
        ),
    )
    supports_video: bool = Field(
        default=False,
        description=(
            "Model can process video input natively. "
            "Currently no standard provider supports this."
        ),
    )
    supports_reasoning: bool = Field(
        default=False,
        description=(
            "Model has explicit reasoning / chain-of-thought capability. "
            "Useful for complex multi-step analysis and planning tasks."
        ),
    )
    image_how: str = Field(
        default="",
        description=(
            "How to pass images when supported, e.g. "
            "'image_url in content array (base64 or URL)'"
        ),
    )
    pdf_how: str = Field(
        default="",
        description=(
            "How to pass PDFs when supported, e.g. "
            "'file input (base64/file-id/URL) in Responses API'"
        ),
    )
    video_how: str = Field(
        default="extract frames as images + optional ASR transcript",
        description="Recommended workaround for video input.",
    )


# ---------------------------------------------------------------------------
# Model entry
# ---------------------------------------------------------------------------


class ModelEntry(BaseModel):
    """A single model registered in the gateway catalog.

    Can represent a cloud API model, a self-hosted endpoint, or a local CLI
    binary.
    """

    model_id: str = Field(..., description="Canonical model name used in API calls.")
    provider: str = Field(..., description="Provider key (must match a ProviderConfig.provider_id).")
    api_style: ApiStyle = Field(
        default=ApiStyle.CHAT_COMPLETIONS,
        description="Wire protocol / API format.",
    )
    endpoint: str = Field(
        ...,
        description=(
            "Full endpoint URL (for HTTP models) or binary path (for CLI models). "
            "If this is a relative path, it is resolved against the provider's base_url."
        ),
    )
    description: str = Field(default="", description="Short model description for logs / UI.")
    max_context_tokens: int | None = Field(
        default=None,
        description="Maximum context window (tokens). None = unknown / provider default.",
    )
    supports_tools: bool = Field(
        default=True,
        description="Whether this model supports tool / function calling.",
    )
    supports_streaming: bool = Field(
        default=True,
        description="Whether this model supports server-sent-event streaming.",
    )
    cost_per_1m_input: float | None = Field(
        default=None,
        description="Approx cost in USD per 1 M input tokens (for budgeting).",
    )
    cost_per_1m_output: float | None = Field(
        default=None,
        description="Approx cost in USD per 1 M output tokens (for budgeting).",
    )
    default_temperature: float = Field(default=0.7, ge=0.0, le=2.0)

    # -- Capability / compliance metadata --
    capabilities: ModelCapabilities = Field(
        default_factory=ModelCapabilities,
        description="Multimodal capability flags (images, PDFs, video).",
    )
    best_for: list[str] = Field(
        default_factory=list,
        description=(
            "Recommended use-cases, e.g. ['drafting', 'summarisation', 'code-review']. "
            "Used by the orchestrator to match tasks to models."
        ),
    )
    limits: list[str] = Field(
        default_factory=list,
        description=(
            "Known limitations, e.g. ['text-only', 'no tool-calling', 'high latency']. "
            "Used by the orchestrator to avoid unsuitable models."
        ),
    )
    compliance: list[str] = Field(
        default_factory=list,
        description=(
            "Compliance / data-handling tags, e.g. ['free-tier', 'no-data-retention', "
            "'public-api']. Used for policy enforcement."
        ),
    )

    # -- CLI fields --
    cli_args: list[str] = Field(
        default_factory=list,
        description="Extra CLI arguments (only used when api_style == CLI).",
    )
    cli_prompt_flag: str | None = Field(
        default=None,
        description=(
            "When set, the prompt is passed as this CLI flag (e.g. '-p') "
            "instead of piped via stdin.  Used by copilot-style CLIs."
        ),
    )
    cli_model_flag: str | None = Field(
        default=None,
        description=(
            "When set, the model name is injected as this CLI flag "
            "(e.g. '--model').  The actual value comes from "
            "extra['cli_model_name'] or model_id."
        ),
    )
    extra: dict[str, Any] = Field(
        default_factory=dict,
        description="Arbitrary provider-specific metadata.",
    )


# ---------------------------------------------------------------------------
# Model registry
# ---------------------------------------------------------------------------


class ModelRegistry:
    """In-memory catalog of available LLM models.

    Thread-safe for reads (dict lookup).  Registration is expected to happen
    once at startup.  The singleton ``MODEL_REGISTRY`` is pre-populated with
    built-in models.
    """

    def __init__(self) -> None:
        self._models: dict[str, ModelEntry] = {}
        self._providers: dict[str, ProviderConfig] = {}

    # -- providers --

    def register_provider(self, config: ProviderConfig) -> None:
        """Register or update a provider configuration."""
        self._providers[config.provider_id] = config

    def get_provider(self, provider_id: str) -> ProviderConfig | None:
        return self._providers.get(provider_id)

    def list_providers(self) -> list[ProviderConfig]:
        return list(self._providers.values())

    # -- models --

    def register(self, entry: ModelEntry) -> None:
        """Register or update a model entry."""
        self._models[entry.model_id] = entry

    def get(self, model_id: str) -> ModelEntry | None:
        """Look up a model by its canonical ID."""
        return self._models.get(model_id)

    def list_models(self, provider: str | None = None) -> list[ModelEntry]:
        """Return all registered models, optionally filtered by provider."""
        if provider is None:
            return list(self._models.values())
        return [m for m in self._models.values() if m.provider == provider]

    def model_ids(self) -> list[str]:
        """Return sorted list of all registered model IDs."""
        return sorted(self._models.keys())

    def __contains__(self, model_id: str) -> bool:
        return model_id in self._models

    def __len__(self) -> int:
        return len(self._models)

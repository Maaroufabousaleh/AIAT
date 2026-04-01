"""Base classes for the provider registry.

This module contains the core types shared by all provider implementations:
``ApiStyle``, ``ProviderConfig``, ``ModelEntry``, ``ModelPool``, and
``ModelRegistry``.
"""

from __future__ import annotations

import os
import time
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
# Model pool — load-balanced rotation across equivalent models
# ---------------------------------------------------------------------------


class ModelPool:
    """Round-robin pool with per-model rate/token tracking.

    When multiple models share a provider where exceeding *any* model's
    individual limit disables *all* models (e.g. Google AI Studio Gemma
    free tier), the pool spreads load evenly and stops routing to a model
    before it reaches the danger zone.

    Parameters
    ----------
    pool_id:
        Virtual model name agents use (e.g. ``"gemma-pool"``).
    model_ids:
        Ordered list of concrete ``ModelEntry.model_id`` values.
    rpm_per_model:
        Per-model requests-per-minute limit.
    rpd_per_model:
        Per-model requests-per-day limit.
    tpm_per_model:
        Per-model tokens-per-minute limit.
    safety_margin:
        Fraction of each limit to reserve (0.0–1.0).  Default 0.15 means
        the pool treats 85 % of the real limit as the effective ceiling.
    description:
        Human-readable description.
    """

    def __init__(
        self,
        pool_id: str,
        model_ids: list[str],
        *,
        rpm_per_model: int = 30,
        rpd_per_model: int = 14_000,
        tpm_per_model: int = 15_000,
        safety_margin: float = 0.15,
        description: str = "",
    ) -> None:
        self.pool_id = pool_id
        self.model_ids = list(model_ids)
        self.rpm_per_model = rpm_per_model
        self.rpd_per_model = rpd_per_model
        self.tpm_per_model = tpm_per_model
        self.safety_margin = safety_margin
        self.description = description

        # Effective limits (after safety margin)
        self._eff_rpm = int(rpm_per_model * (1 - safety_margin))
        self._eff_rpd = int(rpd_per_model * (1 - safety_margin))
        self._eff_tpm = int(tpm_per_model * (1 - safety_margin))

        # Per-model counters: {model_id: [(timestamp, tokens), ...]}
        self._minute_log: dict[str, list[tuple[float, int]]] = {
            m: [] for m in model_ids
        }
        # Per-model daily request counter: {model_id: [(timestamp,), ...]}
        self._day_log: dict[str, list[float]] = {
            m: [] for m in model_ids
        }

        # Round-robin index
        self._rr_idx = 0

    # ----- housekeeping ---------------------------------------------------

    def _prune_minute(self, model_id: str, now: float) -> None:
        """Remove entries older than 60 s from the minute log."""
        cutoff = now - 60.0
        log = self._minute_log[model_id]
        # Find first index still within the window
        i = 0
        while i < len(log) and log[i][0] < cutoff:
            i += 1
        if i:
            del log[:i]

    def _prune_day(self, model_id: str, now: float) -> None:
        """Remove entries older than 86 400 s from the day log."""
        cutoff = now - 86_400.0
        log = self._day_log[model_id]
        i = 0
        while i < len(log) and log[i] < cutoff:
            i += 1
        if i:
            del log[:i]

    # ----- usage querying -------------------------------------------------

    def _minute_requests(self, model_id: str, now: float) -> int:
        self._prune_minute(model_id, now)
        return len(self._minute_log[model_id])

    def _minute_tokens(self, model_id: str, now: float) -> int:
        self._prune_minute(model_id, now)
        return sum(t for _, t in self._minute_log[model_id])

    def _day_requests(self, model_id: str, now: float) -> int:
        self._prune_day(model_id, now)
        return len(self._day_log[model_id])

    def _headroom(self, model_id: str, now: float) -> float:
        """Return a 0–1 score representing how much capacity remains.

        1.0 = completely idle, 0.0 = at the effective limit on at least
        one dimension.  Negative = over limit.
        """
        rpm_used_frac = self._minute_requests(model_id, now) / max(self._eff_rpm, 1)
        tpm_used_frac = self._minute_tokens(model_id, now) / max(self._eff_tpm, 1)
        rpd_used_frac = self._day_requests(model_id, now) / max(self._eff_rpd, 1)
        worst = max(rpm_used_frac, tpm_used_frac, rpd_used_frac)
        return 1.0 - worst

    # ----- selection ------------------------------------------------------

    def pick(self) -> str | None:
        """Choose the best model to use right now.

        Strategy:
        1.  Check all models for headroom (capacity remaining).
        2.  Among models with positive headroom, use round-robin to spread
            load evenly (avoids hot-spotting on the least-used model).
        3.  If no model has headroom, return ``None`` (caller should raise).

        Returns
        -------
        str or None
            Concrete ``model_id`` or ``None`` if all models are exhausted.
        """
        now = time.monotonic()
        n = len(self.model_ids)
        if n == 0:
            return None

        # Try round-robin starting from current index
        for offset in range(n):
            idx = (self._rr_idx + offset) % n
            mid = self.model_ids[idx]
            if self._headroom(mid, now) > 0.0:
                self._rr_idx = (idx + 1) % n
                return mid

        # All models at limit
        return None

    # ----- recording ------------------------------------------------------

    def record_request(self, model_id: str, tokens: int = 0) -> None:
        """Record a completed request for accounting.

        Call this *after* a successful LLM call so the next ``pick()``
        reflects the updated counters.

        Parameters
        ----------
        model_id:
            The concrete model that was used.
        tokens:
            Total tokens consumed (prompt + completion).
        """
        now = time.monotonic()
        if model_id in self._minute_log:
            self._minute_log[model_id].append((now, tokens))
        if model_id in self._day_log:
            self._day_log[model_id].append(now)

    # ----- diagnostics ----------------------------------------------------

    def stats(self) -> dict[str, Any]:
        """Return per-model usage snapshot for logging / debugging."""
        now = time.monotonic()
        result: dict[str, Any] = {
            "pool_id": self.pool_id,
            "effective_limits": {
                "rpm": self._eff_rpm,
                "tpm": self._eff_tpm,
                "rpd": self._eff_rpd,
            },
            "models": {},
        }
        for mid in self.model_ids:
            result["models"][mid] = {
                "rpm_used": self._minute_requests(mid, now),
                "tpm_used": self._minute_tokens(mid, now),
                "rpd_used": self._day_requests(mid, now),
                "headroom": round(self._headroom(mid, now), 3),
            }
        return result

    def reset(self) -> None:
        """Clear all counters (useful for testing)."""
        for mid in self.model_ids:
            self._minute_log[mid].clear()
            self._day_log[mid].clear()
        self._rr_idx = 0


# ---------------------------------------------------------------------------
# Model registry
# ---------------------------------------------------------------------------


class ModelRegistry:
    """In-memory catalog of available LLM models and model pools.

    Thread-safe for reads (dict lookup).  Registration is expected to happen
    once at startup.  The singleton ``MODEL_REGISTRY`` is pre-populated with
    built-in models.
    """

    def __init__(self) -> None:
        self._models: dict[str, ModelEntry] = {}
        self._providers: dict[str, ProviderConfig] = {}
        self._pools: dict[str, ModelPool] = {}

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
        return model_id in self._models or model_id in self._pools

    def __len__(self) -> int:
        return len(self._models)

    # -- pools --

    def register_pool(self, pool: ModelPool) -> None:
        """Register a model pool (virtual load-balanced group)."""
        self._pools[pool.pool_id] = pool

    def get_pool(self, pool_id: str) -> ModelPool | None:
        """Return a registered pool, or None."""
        return self._pools.get(pool_id)

    def resolve_pool(self, model_id: str) -> tuple[ModelEntry | None, ModelPool | None]:
        """If *model_id* is a pool, pick a concrete model and return both.

        Returns ``(entry, pool)`` where:
        - If *model_id* is a pool → ``entry`` is the picked model's entry
          (or ``None`` if all exhausted), ``pool`` is the pool object.
        - If *model_id* is a plain model → ``(entry, None)``.
        - If unknown → ``(None, None)``.
        """
        pool = self._pools.get(model_id)
        if pool is not None:
            picked = pool.pick()
            if picked is None:
                return None, pool
            return self._models.get(picked), pool
        return self._models.get(model_id), None

    def list_pools(self) -> list[ModelPool]:
        """Return all registered pools."""
        return list(self._pools.values())

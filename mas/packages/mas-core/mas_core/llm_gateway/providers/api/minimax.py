"""Unregistered MiniMax candidate metadata.

The MiniMax API is fully **OpenAI-compatible** at
``/v1/chat/completions``, so models registered here use the standard
``CHAT_COMPLETIONS`` API style with no changes to the gateway client.

Authentication uses a standard ``Authorization: Bearer <key>`` header,
read from the ``MINIMAX_API_KEY`` environment variable.

This module deliberately has no registry side effects. The endpoint and
pricing metadata have not been production-validated, so importing built-in
providers must not advertise this candidate to callers.

Candidate models defined here but not registered:
- minimax-2.7     — flagship general-purpose chat model (paid tier)

Cost numbers are visible placeholders (1.0 / 1.0) — NOT real pricing.
Update them once the provider's published per-token rates are confirmed.
The 1500 requests/day cap is a known rate limit.
"""

from __future__ import annotations

from ..base import ApiStyle, ModelCapabilities, ModelEntry, ProviderConfig

# ---------------------------------------------------------------------------
# Provider
# ---------------------------------------------------------------------------

MINIMAX_BASE_URL = "https://api.minimax.io/v1"

MINIMAX_PROVIDER = ProviderConfig(
    provider_id="minimax",
    base_url=MINIMAX_BASE_URL,
    api_key_env_vars=["MINIMAX_API_KEY"],
    description=(
        "MiniMax — OpenAI-compatible inference endpoint. "
        "Set MINIMAX_API_KEY. base_url is a placeholder — update to the "
        "real published endpoint before use."
    ),
)

# ---------------------------------------------------------------------------
# Endpoint constant (all models share the same chat/completions path)
# ---------------------------------------------------------------------------

_CC = f"{MINIMAX_BASE_URL}/chat/completions"

# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------

# ---- MiniMax 2.7 (flagship) -----------------------------------------------

MINIMAX_MODEL_CANDIDATE = ModelEntry(
    model_id="minimax-2.7",
    provider="minimax",
    api_style=ApiStyle.CHAT_COMPLETIONS,
    endpoint=_CC,
    description=(
        "MiniMax 2.7 — unvalidated candidate for a future provider integration."
    ),
    max_context_tokens=128_000,
    supports_tools=True,
    supports_streaming=True,
    cost_per_1m_input=1.0,
    cost_per_1m_output=1.0,
    default_temperature=0.7,
    capabilities=ModelCapabilities(
        supports_images=False,
        supports_pdf=False,
        supports_video=False,
        supports_reasoning=False,
        image_how="not supported — text-only model",
        pdf_how="extract text and send as message content",
    ),
    best_for=["general-purpose", "chat", "tool-calling"],
    limits=["endpoint-and-pricing-unvalidated"],
    compliance=["not-production-certified"],
    extra={"api_model_name": "minimax-2.7", "registered": False},
)

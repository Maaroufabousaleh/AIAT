"""Provider registry — scalable multi-provider model catalog for the LLM gateway.

Architecture
------------
The gateway supports **any** LLM backend that speaks one of the known API
styles.  Each model is registered as a ``ModelEntry`` that declares:

- which provider it belongs to (OpenAI, Zen, Ollama, a local CLI wrapper, …)
- the API style to use (``chat_completions``, ``responses``, or ``cli``)
- the full endpoint URL or a provider-relative path
- descriptive metadata for logging / UI

Adding a new model is a single call::

    MODEL_REGISTRY.register(
        ModelEntry(
            model_id="my-local-llama",
            provider="ollama",
            api_style=ApiStyle.CHAT_COMPLETIONS,
            endpoint="http://localhost:11434/v1/chat/completions",
            description="Local LLaMA via Ollama",
        )
    )

Adding a CLI-based model (e.g. ``llama.cpp``)::

    MODEL_REGISTRY.register(
        ModelEntry(
            model_id="llama-cpp-q4",
            provider="cli",
            api_style=ApiStyle.CLI,
            endpoint="llama-cli",            # binary name / path
            cli_args=["--model", "/models/q4.gguf", "--ctx-size", "4096"],
            description="llama.cpp Q4 quantisation",
        )
    )

Sub-packages
------------
``providers.api``
    HTTP/API-based providers (OpenAI, Zen, …).
``providers.cli``
    CLI/subprocess-based providers (Copilot, llama.cpp, …).
"""

from __future__ import annotations

from .base import (
    ApiStyle,
    ModelCapabilities,
    ModelEntry,
    ModelPool,
    ModelRegistry,
    ProviderConfig,
)

# The global singleton — must exist before sub-packages register into it.
MODEL_REGISTRY = ModelRegistry()

# Import sub-packages so their module-level registration code runs.
from . import api as api  # noqa: E402
from . import cli as cli  # noqa: E402

__all__ = [
    "ApiStyle",
    "ModelCapabilities",
    "ModelEntry",
    "ModelPool",
    "ModelRegistry",
    "ProviderConfig",
    "MODEL_REGISTRY",
]

"""API-based LLM providers (HTTP/REST).

Importing this package registers all built-in API providers and their models
into the global ``MODEL_REGISTRY``.
"""

from . import openai as openai  # noqa: F401
from . import zen as zen  # noqa: F401

"""API-based LLM providers (HTTP/REST).

Importing this package registers all built-in API providers and their models
into the global ``MODEL_REGISTRY``.
"""

from . import cerebras as cerebras  # noqa: F401
from . import cloudflare as cloudflare  # noqa: F401
from . import gemini as gemini  # noqa: F401
from . import groq as groq  # noqa: F401
from . import mistral as mistral  # noqa: F401
from . import openrouter as openrouter  # noqa: F401
from . import zen as zen  # noqa: F401

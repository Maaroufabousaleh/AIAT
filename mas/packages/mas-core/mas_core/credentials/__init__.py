"""Credentials Manager — centralised secret store for the MAS platform.

Secrets are stored encrypted in Postgres.  The rest of the system works
only with named *references* (e.g. ``LLM_API``).  Real values are resolved
only inside the approved execution path by the ``CredentialsManager``.

Usage::

    from mas_core.credentials import CredentialsManager

    mgr = CredentialsManager(encryption_key=b"...", storage=storage)
    ref  = await mgr.create("OPENAI_KEY", "sk-...", secret_type="api_key")
    val  = await mgr.resolve("OPENAI_KEY", context={"requester": "llm-gateway"})
"""

from .manager import CredentialsManager
from .models import SecretMetadata, SecretPolicy, SecretType

__all__ = ["CredentialsManager", "SecretMetadata", "SecretPolicy", "SecretType"]

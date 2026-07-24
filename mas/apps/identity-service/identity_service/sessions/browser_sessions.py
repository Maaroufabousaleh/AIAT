"""Browser-session policy for local persistent worker/service profiles."""

from __future__ import annotations

import hashlib
from pathlib import Path
from uuid import UUID


def profile_key(worker_id: UUID, service: str) -> str:
    """Stable opaque profile key; never a path received from a worker."""
    normalized = service.strip().lower()
    digest = hashlib.sha256(f"{worker_id}:{normalized}".encode()).hexdigest()[:32]
    return f"browser-profile-{digest}"


def profile_path(root: Path, worker_id: UUID, service: str) -> Path:
    """Constrain local profiles to a configured root and prevent traversal."""
    root = root.resolve()
    path = (root / profile_key(worker_id, service)).resolve()
    if root not in path.parents:
        raise ValueError("browser profile path escaped configured root")
    return path

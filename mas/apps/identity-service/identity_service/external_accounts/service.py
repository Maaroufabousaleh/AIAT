"""Default-deny external-service category policy."""

from __future__ import annotations

from dataclasses import dataclass


class ExternalAccountPolicyError(PermissionError):
    pass


@dataclass(frozen=True)
class ExternalAccountPolicy:
    allowed_categories: frozenset[str] = frozenset({"development_test"})
    approval_categories: frozenset[str] = frozenset({"github_organization", "google", "microsoft"})

    def disposition(self, category: str) -> str:
        normalized = category.strip().lower()
        if normalized in self.allowed_categories:
            return "allowed"
        if normalized in self.approval_categories:
            return "approval_required"
        raise ExternalAccountPolicyError("external service category is denied by default")

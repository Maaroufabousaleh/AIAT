"""External-account category and high-risk action policy.

The identity service keeps the action taxonomy explicit so callers can render
the human gate before invoking an external-account operation.  This is a
control-plane policy, not a provider-specific implementation: provider live
certification remains a separate concern.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar


class ExternalAccountPolicyError(PermissionError):
    pass


@dataclass(frozen=True)
class ExternalAccountActionRule:
    action: str
    risk: str
    approval_required: bool
    approval_kind: str | None
    disposition: str
    description: str


@dataclass(frozen=True)
class ExternalAccountPolicy:
    allowed_categories: frozenset[str] = frozenset({"development_test"})
    approval_categories: frozenset[str] = frozenset({"github_organization", "google", "microsoft"})

    _ACTION_RULES: ClassVar[tuple[ExternalAccountActionRule, ...]] = (
        ExternalAccountActionRule(
            action="signup",
            risk="category_sensitive",
            approval_required=True,
            approval_kind="external_account",
            disposition="category_dependent",
            description=(
                "Account activation is immediate only for the development_test "
                "category; other categories remain pending until a human decision."
            ),
        ),
        ExternalAccountActionRule(
            action="rotate_credentials",
            risk="high",
            approval_required=True,
            approval_kind="external_credential_rotation",
            disposition="approval_required",
            description=(
                "Credential rotation invalidates existing browser sessions and "
                "always pauses for a human approval."
            ),
        ),
        ExternalAccountActionRule(
            action="close",
            risk="high",
            approval_required=True,
            approval_kind="external_account_close",
            disposition="approval_required",
            description=(
                "Closing an account is irreversible in the control plane and "
                "pauses for a human approval before session revocation."
            ),
        ),
        ExternalAccountActionRule(
            action="suspend",
            risk="safety",
            approval_required=False,
            approval_kind=None,
            disposition="immediate",
            description=(
                "Suspension is an emergency revocation action and is allowed "
                "immediately so access can be cut off."
            ),
        ),
        ExternalAccountActionRule(
            action="browser_session",
            risk="controlled",
            approval_required=False,
            approval_kind=None,
            disposition="governed_account_required",
            description=(
                "A local browser session requires an already-approved active "
                "account and short-lived broker lease; it does not export credentials."
            ),
        ),
    )

    def disposition(self, category: str) -> str:
        normalized = category.strip().lower()
        if normalized in self.allowed_categories:
            return "allowed"
        if normalized in self.approval_categories:
            return "approval_required"
        raise ExternalAccountPolicyError("external service category is denied by default")

    def action_rule(self, action: str) -> ExternalAccountActionRule:
        normalized = action.strip().lower()
        for rule in self._ACTION_RULES:
            if rule.action == normalized:
                return rule
        raise ExternalAccountPolicyError("external account action is denied by default")

    def action_catalog(self) -> dict[str, object]:
        return {
            "schema_version": "aiat.external-account-action-policy.v1",
            "actions": [
                {
                    "action": rule.action,
                    "risk": rule.risk,
                    "approval_required": rule.approval_required,
                    "approval_kind": rule.approval_kind,
                    "disposition": rule.disposition,
                    "description": rule.description,
                }
                for rule in self._ACTION_RULES
            ],
            "category_policy": {
                "allowed_categories": sorted(self.allowed_categories),
                "approval_categories": sorted(self.approval_categories),
                "unknown_categories": "denied",
            },
        }

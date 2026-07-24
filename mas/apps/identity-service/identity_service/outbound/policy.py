"""Default-deny outbound recipient and size policy."""

from __future__ import annotations

from dataclasses import dataclass


class OutboundPolicyError(PermissionError):
    pass


@dataclass(frozen=True)
class OutboundPolicy:
    max_recipients: int = 50
    max_body_bytes: int = 250_000
    allowed_recipient_classes: frozenset[str] = frozenset({"approved_external", "internal_agent"})

    def validate(self, *, recipients: list[str], recipient_class: str, body: str, sender_domain: str) -> None:
        if recipient_class not in self.allowed_recipient_classes:
            raise OutboundPolicyError("unknown outbound recipient class is denied")
        if not recipients or len(recipients) > self.max_recipients:
            raise OutboundPolicyError("outbound recipient count is not allowed")
        if len(body.encode("utf-8")) > self.max_body_bytes:
            raise OutboundPolicyError("outbound content exceeds policy limit")
        if recipient_class == "internal_agent" and any(not item.lower().endswith("@" + sender_domain) for item in recipients):
            raise OutboundPolicyError("internal agent recipient class requires agent-domain recipients")

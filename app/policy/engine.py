"""STUB (Phase 0). Person B implements in Phase 1."""
from __future__ import annotations

from app.contracts import AuditEntry, Decision, MailboxProtocol, ProposedAction


class PolicyEngine:
    def __init__(self, rules_path: str = "rules.yaml") -> None:
        self.rules_path = rules_path

    def evaluate(self, action: ProposedAction, mailbox: MailboxProtocol,
                 history: list[AuditEntry] | None = None) -> Decision:
        raise NotImplementedError("Phase 1.B2")

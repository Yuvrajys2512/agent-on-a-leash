"""STUB (Phase 0). Person B implements in Phase 1."""
from app.contracts import Decision, MailboxProtocol, ProposedAction


class PolicyEngine:
    def __init__(self, rules_path: str = "rules.yaml"):
        self.rules_path = rules_path

    def evaluate(self, action: ProposedAction, mailbox: MailboxProtocol) -> Decision:
        raise NotImplementedError

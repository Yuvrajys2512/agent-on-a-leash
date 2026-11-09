"""Policy engine (Phase 1.B2). Owned by Person B.

First matching rule wins. If nothing matches, or anything goes wrong, the answer is ASK (fail safe).
"""
from __future__ import annotations

from app.contracts import (
    ActionType, Decision, MailboxProtocol, ProposedAction, Verdict, is_internal,
)
from app.policy.rules import Rule, load_rules

DEFAULT_RULE_ID = "default-ask"


class PolicyEngine:
    def __init__(self, rules_path: str = "rules.yaml"):
        self.rules_path = rules_path
        self.rules: list[Rule] = load_rules(rules_path).rules

    def recipient_of(self, action: ProposedAction, mailbox: MailboxProtocol) -> str | None:
        """Who would receive this? None if the action sends nothing."""
        if action.type == ActionType.FORWARD:
            return action.params.get("to") or ""
        if action.type == ActionType.REPLY:
            return mailbox.get(action.email_id).sender
        return None

    def compute_flags(self, action: ProposedAction, mailbox: MailboxProtocol) -> list[str]:
        flags: list[str] = []
        recipient = self.recipient_of(action, mailbox)
        if recipient is not None:
            # empty/unknown recipient is treated as external (fail safe)
            flags.append("internal_recipient" if recipient and is_internal(recipient) else "external_recipient")
        return flags

    @staticmethod
    def _matches(rule: Rule, action: ProposedAction, flags: list[str]) -> bool:
        w = rule.when
        if w.action_type and action.type not in w.action_type:
            return False
        if w.recipient == "internal" and "internal_recipient" not in flags:
            return False
        if w.recipient == "external" and "external_recipient" not in flags:
            return False
        return all(f in flags for f in w.flags_all)

    def evaluate(self, action: ProposedAction, mailbox: MailboxProtocol) -> Decision:
        try:
            flags = self.compute_flags(action, mailbox)
        except Exception as e:  # e.g. unknown email id -> never allow silently
            return Decision(verdict=Verdict.ASK, rule_id="evaluation-error",
                            reason=f"Could not evaluate action safely ({e}); asking the human.", flags=["error"])
        for rule in self.rules:
            if self._matches(rule, action, flags):
                return Decision(verdict=rule.verdict, rule_id=rule.id, reason=rule.reason, flags=flags)
        return Decision(verdict=Verdict.ASK, rule_id=DEFAULT_RULE_ID,
                        reason="No rule covers this action, so the human is asked.", flags=flags)

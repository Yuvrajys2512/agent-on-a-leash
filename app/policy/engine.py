"""Policy engine (Phase 1.B2). Owned by Person B.

First matching rule wins. If nothing matches, or anything goes wrong, the answer is ASK (fail safe).
`history` (earlier audit entries of the same run) is accepted now and used from Phase 4 on.
"""
from __future__ import annotations

from app.contracts import (
    ActionType, AuditEntry, Decision, MailboxProtocol, ProposedAction, RuleHit, Verdict, is_internal,
)
from app.policy.rules import Rule, load_rules

DEFAULT_RULE_ID = "default-ask"


class PolicyEngine:
    def __init__(self, rules_path: str = "rules.yaml") -> None:
        self.rules_path = rules_path
        self.rules: list[Rule] = load_rules(rules_path).rules

    def recipients_of(self, action: ProposedAction, mailbox: MailboxProtocol) -> list[str] | None:
        """Everyone who would receive this. None if the action sends nothing."""
        if action.type == ActionType.FORWARD:
            to = action.params.get("to") or []
            return [to] if isinstance(to, str) else list(to)
        if action.type == ActionType.REPLY:
            return [mailbox.get(action.email_id).sender]
        return None

    def compute_flags(self, action: ProposedAction, mailbox: MailboxProtocol) -> list[str]:
        flags: list[str] = []
        recipients = self.recipients_of(action, mailbox)
        if recipients is not None:
            # No recipients at all counts as external (fail safe). ONE external recipient makes it external.
            if recipients and all(is_internal(r) for r in recipients):
                flags.append("internal_recipient")
            else:
                flags.append("external_recipient")
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

    def evaluate(self, action: ProposedAction, mailbox: MailboxProtocol,
                 history: list[AuditEntry] | None = None) -> Decision:
        try:
            flags = self.compute_flags(action, mailbox)
        except Exception as e:  # e.g. unknown email id -> never allow silently
            reason = f"Could not evaluate action safely ({e}); asking the human."
            return Decision(verdict=Verdict.ASK, rule_id="evaluation-error", reason=reason, flags=["error"],
                            hits=[RuleHit(rule_id="evaluation-error", verdict=Verdict.ASK, reason=reason)])
        hits = [RuleHit(rule_id=r.id, verdict=r.verdict, reason=r.reason)
                for r in self.rules if self._matches(r, action, flags)]
        if hits:
            first = hits[0]  # first match decides; the rest are kept so the log can show every reason
            return Decision(verdict=first.verdict, rule_id=first.rule_id, reason=first.reason, flags=flags, hits=hits)
        reason = "No rule covers this action, so the human is asked."
        return Decision(verdict=Verdict.ASK, rule_id=DEFAULT_RULE_ID, reason=reason, flags=flags,
                        hits=[RuleHit(rule_id=DEFAULT_RULE_ID, verdict=Verdict.ASK, reason=reason)])

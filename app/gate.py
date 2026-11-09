"""The gate (Phase 2.B1). Owned by Person B.

Every action the agent proposes passes through here:
    policy decides -> (ask? wait for the human) -> execute on the mailbox -> write ONE audit entry.
Nothing is ever executed unless policy said allow, or the human approved.
"""
from __future__ import annotations

from app.approvals import ApprovalStore
from app.audit.log import AuditLog
from app.contracts import (
    AuditEntry, Decision, Gate, GateResult, MailboxProtocol, Outcome, ProposedAction, RuleHit, Verdict,
)
from app.policy.engine import PolicyEngine


def make_gate(policy: PolicyEngine, mailbox: MailboxProtocol, audit: AuditLog, approvals: ApprovalStore) -> Gate:
    history: list[AuditEntry] = []  # this run's earlier entries, given to the policy engine

    def finish(action: ProposedAction, decision: Decision, approval, outcome: Outcome, detail: str) -> GateResult:
        entry = AuditEntry(action=action, decision=decision, approval=approval, outcome=outcome, detail=detail)
        audit.record(entry)
        history.append(entry)
        result = GateResult(outcome=outcome, decision=decision, detail=detail)
        approvals.emit("done", entry=entry.model_dump(mode="json"))
        return result

    async def gate(action: ProposedAction) -> GateResult:
        try:
            decision = policy.evaluate(action, mailbox, history)
        except Exception as e:  # policy crashed -> never allow
            reason = f"Policy failed ({e}); asking the human."
            decision = Decision(verdict=Verdict.ASK, rule_id="policy-error", reason=reason, flags=["error"],
                                hits=[RuleHit(rule_id="policy-error", verdict=Verdict.ASK, reason=reason)])
        approvals.emit("proposed", action=action.model_dump(mode="json"), decision=decision.model_dump(mode="json"))

        if decision.verdict == Verdict.BLOCK:
            return finish(action, decision, None, Outcome.BLOCKED,
                          f"Blocked by rule '{decision.rule_id}': {decision.reason} Do not retry this action.")

        if decision.verdict == Verdict.ASK:
            if not await approvals.request(action, decision):
                return finish(action, decision, "rejected", Outcome.REJECTED_BY_HUMAN,
                              "The human rejected this action (or did not answer in time).")
            approval = "approved"
        else:
            approval = "auto"

        try:
            description = mailbox.execute(action)
        except Exception as e:
            return finish(action, decision, approval, Outcome.FAILED, f"Action failed: {e}")
        return finish(action, decision, approval, Outcome.EXECUTED, description)

    return gate

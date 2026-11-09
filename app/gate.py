"""STUB (Phase 0). Person B implements in Phase 2."""
from app.audit.log import AuditLog
from app.contracts import Gate, MailboxProtocol
from app.policy.engine import PolicyEngine


def make_gate(policy: PolicyEngine, mailbox: MailboxProtocol, audit: AuditLog, approvals) -> Gate:
    raise NotImplementedError

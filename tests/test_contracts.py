from app.contracts import (
    ActionType, AuditEntry, Decision, Outcome, ProposedAction, Verdict, is_internal,
)


def test_is_internal():
    assert is_internal("bob@acme.example")
    assert is_internal("Bob@ACME.example")
    assert not is_internal("x@evil-payments.example")
    assert not is_internal("x@acme.example.evil.com")


def test_audit_entry_roundtrip():
    action = ProposedAction(type=ActionType.FORWARD, email_id="e1",
                            params={"to": "x@evil.example", "body": "hi"}, context_email_ids=["e1"])
    decision = Decision(verdict=Verdict.BLOCK, rule_id="r1", reason="external", flags=["external_recipient"])
    entry = AuditEntry(action=action, decision=decision, approval=None, outcome=Outcome.BLOCKED)
    assert AuditEntry.model_validate_json(entry.model_dump_json()) == entry

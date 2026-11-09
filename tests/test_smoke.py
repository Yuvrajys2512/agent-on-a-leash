from app.contracts import ActionType, ProposedAction, Verdict, is_internal
from app.mailbox import Mailbox
from app.agent import run_agent


def test_contracts_and_stubs_import():
    a = ProposedAction(type=ActionType.FORWARD, email_id="e1", params={"to": ["x@evil.example"]})
    assert a.id and a.type is ActionType.FORWARD
    assert Verdict("block") is Verdict.BLOCK
    assert is_internal("ann@ACME.example") and not is_internal("x@acme.example.evil.com")
    assert Mailbox().list() == []
    assert callable(run_agent)


def test_decision_keeps_every_rule_hit():
    from app.contracts import Decision, RuleHit
    d = Decision(verdict=Verdict.BLOCK, rule_id="B1", reason="bulk forward",
                 flags=["bulk", "external_recipient"],
                 hits=[RuleHit(rule_id="B1", verdict=Verdict.BLOCK, reason="bulk"),
                       RuleHit(rule_id="B2", verdict=Verdict.BLOCK, reason="external")])
    assert [h.rule_id for h in d.hits] == ["B1", "B2"]

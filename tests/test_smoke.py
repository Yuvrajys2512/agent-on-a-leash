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

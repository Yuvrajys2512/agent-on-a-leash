from app.contracts import ActionType, ProposedAction
from app.mailbox import Mailbox


def test_reset_restores_the_original_ten():
    mb = Mailbox()
    original = mb.snapshot()
    mb.execute(ProposedAction(type=ActionType.MOVE, email_id="e05", params={"folder": "trash"}))
    mb.execute(ProposedAction(type=ActionType.REPLY, email_id="e06", params={"body": "Sorry!"}))
    assert mb.snapshot() != original
    mb.reset()
    assert mb.snapshot() == original
    assert len(mb.list(folder="inbox")) == 10 and mb.outbox == []


def test_snapshot_is_a_copy():
    mb = Mailbox()
    snap = mb.snapshot()
    mb.execute(ProposedAction(type=ActionType.LABEL, email_id="e01", params={"label": "invoice"}))
    assert snap["emails"][0]["labels"] == []

import pytest

from app.contracts import ActionType, ProposedAction
from app.mailbox import Mailbox


def act(t, email_id, **params):
    return ProposedAction(type=t, email_id=email_id, params=params)


def test_label_adds_once():
    mb = Mailbox()
    mb.execute(act(ActionType.LABEL, "e01", label="invoice"))
    mb.execute(act(ActionType.LABEL, "e01", label="invoice"))
    assert mb.get("e01").labels == ["invoice"] and mb.outbox == []


def test_move_changes_folder():
    mb = Mailbox()
    mb.execute(act(ActionType.MOVE, "e05", folder="newsletters"))
    assert mb.get("e05").folder == "newsletters"
    assert len(mb.list(folder="inbox")) == 9


def test_reply_goes_to_sender_via_outbox_only():
    mb = Mailbox()
    detail = mb.execute(act(ActionType.REPLY, "e04", body="Thursday 14:00 works."))
    [out] = mb.outbox
    assert out["kind"] == "reply" and out["to"] == ["maria.lindqvist@helsinkiventures.example"]
    assert out["subject"] == "Re: Thursday: Q4 pilot review?" and out["body"] == "Thursday 14:00 works."
    assert "not really sent" in detail


def test_forward_quotes_the_original():
    mb = Mailbox()
    mb.execute(act(ActionType.FORWARD, "e01", to="accounting@acme.example", body="FYI"))
    [out] = mb.outbox
    assert out["to"] == ["accounting@acme.example"] and out["subject"].startswith("Fwd: ")
    assert out["body"].startswith("FYI") and "FI21 1234 5600 0007 85" in out["body"]


@pytest.mark.parametrize("t,params", [(ActionType.LABEL, {}), (ActionType.MOVE, {}),
                                      (ActionType.REPLY, {}), (ActionType.FORWARD, {}),
                                      (ActionType.FORWARD, {"to": []})])
def test_missing_params_fail_clearly_and_change_nothing(t, params):
    mb = Mailbox()
    before = mb.snapshot()
    with pytest.raises(ValueError):
        mb.execute(act(t, "e01", **params))
    assert mb.snapshot() == before


def test_unknown_email_fails():
    with pytest.raises(KeyError):
        Mailbox().execute(act(ActionType.LABEL, "e99", label="x"))

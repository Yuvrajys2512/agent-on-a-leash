import pytest

from app.mailbox import Mailbox


def test_loads_all_ten_into_the_inbox():
    mb = Mailbox()
    assert len(mb.list()) == 10
    assert len(mb.list(folder="inbox")) == 10
    assert mb.list(folder="archive") == []


def test_get_returns_the_right_email():
    e = Mailbox().get("e09")
    assert e.sender == "finance@acme-billing.example"
    assert "ignore previous instructions" in e.body


def test_get_unknown_id_says_so():
    with pytest.raises(KeyError, match="e99"):
        Mailbox().get("e99")


def test_each_mailbox_has_its_own_state():
    a, b = Mailbox(), Mailbox()
    a.outbox.append({"x": 1})
    assert b.outbox == []

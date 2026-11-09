from app.contracts import is_internal
from app.mailbox import load


def test_inbox_has_the_planned_mix():
    emails = load()
    assert len(emails) == 10 and len({e.id for e in emails}) == 10
    invoices = [e for e in emails if "invoice" in e.subject.lower() and "routing" not in e.subject.lower()]
    assert len(invoices) == 3
    assert any("IBAN" in e.body and "+358" in e.body for e in invoices)
    assert sum(is_internal(e.sender) for e in emails) == 2
    trick = [e for e in emails if "ignore previous instructions" in e.body.lower()]
    assert len(trick) == 1 and not is_internal(trick[0].sender)

from app.audit.log import AuditLog
from app.contracts import MailboxProtocol
from app.gate import make_gate
from app.mailbox import Mailbox
from app.policy.engine import PolicyEngine


def test_b_stubs_import_and_mailbox_fits_protocol():
    assert callable(make_gate) and PolicyEngine() and AuditLog()
    mb: MailboxProtocol = Mailbox()  # type-level check: A's Mailbox satisfies the protocol
    assert mb.list() is not None

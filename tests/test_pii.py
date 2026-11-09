import pytest

from app.contracts import ActionType as T, ProposedAction, Verdict as V
from app.mailbox import Mailbox
from app.policy.engine import PolicyEngine
from app.policy.pii import find_pii, iban_valid, names_from_addresses, redact


def kinds(text, names=()):
    return [h.kind for h in find_pii(text, names)]


# ---------- IBAN ----------
@pytest.mark.parametrize("iban", [
    "FI21 1234 5600 0007 85", "FI2112345600000785", "fi21 1234 5600 0007 85",
    "DE89 3704 0044 0532 0130 00", "GB82 WEST 1234 5698 7654 32",
])
def test_valid_ibans(iban):
    assert iban_valid(iban) and kinds(f"Pay to {iban}, thanks") == ["iban"]


@pytest.mark.parametrize("bad", ["FI22 1234 5600 0007 85", "FI21 1234 5600 0007 86", "AB12 CDEF GHIJ", "ORDER 1234 5678 9012"])
def test_invalid_ibans_are_not_flagged(bad):
    assert not iban_valid(bad) and "iban" not in kinds(bad)


# ---------- phone ----------
@pytest.mark.parametrize("phone", ["+358 50 123 4567", "+358 40 765 4321", "040 765 4321", "+49 30 1234567", "+44 20 7946 0958"])
def test_phones(phone):
    assert kinds(f"call me on {phone} today") == ["phone"]


@pytest.mark.parametrize("text", [
    "Invoice NP-2026-1182 for EUR 1,240.00, due 23 Nov 2026", "order 4471 arrived", "reference 11823",
    "meet at 14:00 on 13 Nov", "2026-11-09T08:12:00+02:00", "55120", "Call at 09:15",
])
def test_non_phones_are_not_flagged(text):
    assert kinds(text) == []


def test_iban_digits_are_not_also_reported_as_a_phone():
    assert kinds("IBAN FI21 1234 5600 0007 85") == ["iban"]


# ---------- names ----------
def test_names_from_addresses():
    got = names_from_addresses(["pekka.hiltunen@x.example", "sam@acme.example", "it@acme.example", "Maria_Lindqvist@y.example"])
    assert got == {"Pekka Hiltunen", "Maria Lindqvist"}


def test_name_matching_is_case_and_whitespace_tolerant_but_needs_full_name():
    assert kinds("signed pekka  HILTUNEN", {"Pekka Hiltunen"}) == ["name"]
    assert kinds("Pekka said hi", {"Pekka Hiltunen"}) == []
    assert kinds("Pekkarinen Hiltunen", {"Pekka Hiltunen"}) == []


# ---------- redaction ----------
def test_redact_masks_everything_found():
    text = "Hi Pekka Hiltunen, IBAN FI21 1234 5600 0007 85, phone +358 50 123 4567."
    out = redact(text, find_pii(text, {"Pekka Hiltunen"}))
    assert out == "Hi [NAME], IBAN [IBAN], phone [PHONE]."
    assert "1234" not in out and "Hiltunen" not in out


# ---------- the real inbox ----------
def test_real_inbox_finds_exactly_the_expected_personal_data():
    mb = Mailbox()
    names = names_from_addresses(a for e in mb.list() for a in [e.sender, *e.to])
    found = {e.id: sorted(set(kinds(f"{e.subject}\n{e.body}", names))) for e in mb.list()}
    assert {k: v for k, v in found.items() if v} == {"e01": ["iban", "phone"], "e06": ["name", "phone"]}


# ---------- policy ----------
@pytest.fixture
def engine():
    return PolicyEngine("rules.yaml")


@pytest.fixture
def mb():
    return Mailbox()


def fwd(email_id, to):
    return ProposedAction(type=T.FORWARD, email_id=email_id, params={"to": to}, context_email_ids=[email_id])


def test_forwarding_invoice_with_iban_outside_is_blocked_with_masked_preview(engine, mb):
    d = engine.evaluate(fwd("e01", ["partner@outside.example"]), mb)
    assert d.verdict == V.BLOCK and d.rule_id == "block-pii-external-send"
    assert "pii" in d.flags and "pii:iban" in d.flags and "pii:phone" in d.flags
    assert "IBAN" in d.reason and "phone number" in d.reason
    assert "[IBAN]" in d.reason and "1234 5600" not in d.reason and "765 4321" not in d.reason  # preview never shows the data


def test_forwarding_customer_complaint_outside_is_blocked_for_name_and_phone(engine, mb):
    d = engine.evaluate(fwd("e06", ["someone@outside.example"]), mb)
    assert d.verdict == V.BLOCK and "pii:name" in d.flags and "pii:phone" in d.flags
    assert "Pekka Hiltunen" not in d.reason


def test_same_forward_inside_the_company_is_allowed(engine, mb):
    d = engine.evaluate(fwd("e01", ["accounting@acme.example"]), mb)
    assert d.verdict == V.ALLOW and "pii:iban" in d.flags  # flagged for the log, but nothing leaves the company


def test_reply_carrying_a_phone_number_is_blocked(engine, mb):
    a = ProposedAction(type=T.REPLY, email_id="e10", params={"body": "Call our CFO on +358 40 999 8888."}, context_email_ids=["e10"])
    assert engine.evaluate(a, mb).verdict == V.BLOCK


def test_reply_to_customer_by_their_own_name_is_not_a_leak(engine, mb):
    a = ProposedAction(type=T.REPLY, email_id="e06", params={"body": "Dear Pekka Hiltunen, we will send a replacement."},
                       context_email_ids=["e06"])
    d = engine.evaluate(a, mb)
    assert d.verdict == V.ASK and d.rule_id == "external-send-ask" and "pii" not in d.flags


def test_reply_mentioning_a_different_known_person_is_blocked(engine, mb):
    a = ProposedAction(type=T.REPLY, email_id="e10", params={"body": "Maria Lindqvist has the numbers."}, context_email_ids=["e10"])
    d = engine.evaluate(a, mb)
    assert d.verdict == V.BLOCK and "pii:name" in d.flags


def test_injection_block_still_wins_over_pii_block(engine, mb):
    a = ProposedAction(type=T.FORWARD, email_id="e01", params={"to": ["collect@evil-payments.example"]},
                       context_email_ids=["e01", "e09"])
    d = engine.evaluate(a, mb)
    assert d.rule_id == "block-injected-external-send"
    assert "block-pii-external-send" in [h.rule_id for h in d.hits]  # both reasons are kept in the log


def test_ordinary_external_actions_without_personal_data_are_unchanged(engine, mb):
    assert engine.evaluate(fwd("e02", ["partner@outside.example"]), mb).rule_id == "external-send-ask"
    a = ProposedAction(type=T.REPLY, email_id="e06", params={"body": "We will send a replacement."}, context_email_ids=["e06"])
    assert engine.evaluate(a, mb).rule_id == "external-send-ask"

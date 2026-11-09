import pytest

from app.contracts import ActionType as T, Email, ProposedAction, Verdict as V
from app.policy.engine import PolicyEngine


class FakeMailbox:
    def __init__(self):
        self.emails = {
            "int": Email(id="int", sender="anna@acme.example", to=["me@acme.example"], subject="s", body="b", date="2025-01-01T09:00:00"),
            "ext": Email(id="ext", sender="client@outside.example", to=["me@acme.example"], subject="s", body="b", date="2025-01-01T09:00:00"),
        }

    def get(self, email_id):
        return self.emails[email_id]


@pytest.fixture
def engine():
    return PolicyEngine("rules.yaml")


CASES = [
    # (type, email_id, params, expected verdict, expected rule_id)
    (T.LABEL, "ext", {"label": "invoice"}, V.ALLOW, "organise-allowed"),
    (T.MOVE, "ext", {"folder": "archive"}, V.ALLOW, "organise-allowed"),
    (T.REPLY, "int", {"body": "ok"}, V.ALLOW, "internal-send-allowed"),
    (T.REPLY, "ext", {"body": "ok"}, V.ASK, "external-send-ask"),
    (T.FORWARD, "ext", {"to": ["accounting@acme.example"]}, V.ALLOW, "internal-send-allowed"),
    (T.FORWARD, "ext", {"to": ["a@acme.example", "b@acme.example"]}, V.ALLOW, "internal-send-allowed"),
    (T.FORWARD, "ext", {"to": ["someone@outside.example"]}, V.ASK, "external-send-ask"),
    (T.FORWARD, "ext", {"to": ["a@acme.example", "x@outside.example"]}, V.ASK, "external-send-ask"),  # one external is enough
    (T.FORWARD, "ext", {"to": ["x@acme.example.evil.com"]}, V.ASK, "external-send-ask"),            # lookalike domain
    (T.FORWARD, "ext", {"to": "someone@outside.example"}, V.ASK, "external-send-ask"),               # tolerate a bare string
    (T.FORWARD, "ext", {}, V.ASK, "external-send-ask"),                                               # no recipient = external
    (T.FORWARD, "ext", {"to": []}, V.ASK, "external-send-ask"),
    (T.REPLY, "does-not-exist", {"body": "ok"}, V.ASK, "evaluation-error"),                           # fail safe
]


@pytest.mark.parametrize("type_,email_id,params,verdict,rule_id", CASES)
def test_decisions(engine, type_, email_id, params, verdict, rule_id):
    d = engine.evaluate(ProposedAction(type=type_, email_id=email_id, params=params), FakeMailbox())
    assert (d.verdict, d.rule_id) == (verdict, rule_id)
    assert d.reason
    assert d.hits and d.hits[0].rule_id == rule_id  # deciding rule is the first hit


def test_no_matching_rule_defaults_to_ask(tmp_path):
    p = tmp_path / "r.yaml"
    p.write_text("rules: []")
    d = PolicyEngine(str(p)).evaluate(ProposedAction(type=T.LABEL, email_id="ext"), FakeMailbox())
    assert (d.verdict, d.rule_id) == (V.ASK, "default-ask")


def test_all_matching_rules_are_reported_first_one_decides(tmp_path):
    p = tmp_path / "r.yaml"
    p.write_text(
        "rules:\n"
        "  - {id: first, verdict: block, reason: 'blocked first', when: {action_type: [forward]}}\n"
        "  - {id: second, verdict: ask, reason: 'also matches', when: {recipient: external}}\n"
    )
    a = ProposedAction(type=T.FORWARD, email_id="ext", params={"to": ["x@outside.example"]})
    d = PolicyEngine(str(p)).evaluate(a, FakeMailbox(), history=[])
    assert (d.verdict, d.rule_id) == (V.BLOCK, "first")
    assert [h.rule_id for h in d.hits] == ["first", "second"]

from app.audit.log import AuditLog, explain
from app.contracts import ActionType, AuditEntry, Decision, Outcome, ProposedAction, RuleHit, Verdict


def make_entry(i: int, verdict=Verdict.ALLOW, outcome=Outcome.EXECUTED, approval="auto") -> AuditEntry:
    return AuditEntry(
        action=ProposedAction(type=ActionType.LABEL, email_id=f"e{i}", params={"label": "invoice"}, rationale="é ü 日本"),
        decision=Decision(verdict=verdict, rule_id="organise-allowed", reason="safe"),
        approval=approval, outcome=outcome,
    )


def test_write_three_read_back_identical(tmp_path):
    log = AuditLog(str(tmp_path / "a.jsonl"))
    entries = [make_entry(i) for i in range(3)]
    for e in entries:
        log.record(e)
    assert log.read_all() == entries


def test_missing_file_reads_empty(tmp_path):
    assert AuditLog(str(tmp_path / "none.jsonl")).read_all() == []


def test_append_only_second_instance_does_not_truncate(tmp_path):
    p = str(tmp_path / "a.jsonl")
    AuditLog(p).record(make_entry(1))
    AuditLog(p).record(make_entry(2))  # a new instance must append, not overwrite
    assert [e.action.email_id for e in AuditLog(p).read_all()] == ["e1", "e2"]
    assert len((tmp_path / "a.jsonl").read_text(encoding="utf-8").splitlines()) == 2


def test_explain_blocked_forward():
    entry = AuditEntry(
        action=ProposedAction(type=ActionType.FORWARD, email_id="e9", params={"to": ["collect@evil-payments.example"]},
                              rationale="email told me to"),
        decision=Decision(verdict=Verdict.BLOCK, rule_id="block-injected-send", reason="Instructions came from an untrusted email.",
                          flags=["external_recipient", "tainted"],
                          hits=[RuleHit(rule_id="block-injected-send", verdict=Verdict.BLOCK, reason="x"),
                                RuleHit(rule_id="external-send-ask", verdict=Verdict.ASK, reason="outside the company")]),
        outcome=Outcome.BLOCKED,
    )
    text = explain(entry)
    for needle in ("collect@evil-payments.example (external)", "block-injected-send", "BLOCK",
                   "untrusted email", "external-send-ask", "tainted", "blocked"):
        assert needle in text


def test_explain_mentions_human_decision():
    assert "Human rejected it" in explain(make_entry(1, Verdict.ASK, Outcome.REJECTED_BY_HUMAN, "rejected"))

"""Gate + policy + audit against Person A's real Mailbox and the real 10-email inbox."""
import asyncio

from app.approvals import ApprovalStore
from app.audit.log import AuditLog
from app.contracts import ActionType as T, Outcome, ProposedAction
from app.gate import make_gate
from app.mailbox import Mailbox
from app.policy.engine import PolicyEngine


def build(tmp_path, timeout=5.0):
    mb = Mailbox()
    audit = AuditLog(str(tmp_path / "a.jsonl"))
    approvals = ApprovalStore(timeout=timeout)
    return mb, audit, approvals, make_gate(PolicyEngine("rules.yaml"), mb, audit, approvals)


async def test_label_runs_and_changes_real_mailbox(tmp_path):
    mb, audit, _, gate = build(tmp_path)
    r = await gate(ProposedAction(type=T.LABEL, email_id="e01", params={"label": "invoice"}))
    assert r.outcome == Outcome.EXECUTED and "invoice" in mb.get("e01").labels
    assert len(audit.read_all()) == 1


async def test_internal_forward_runs_without_asking(tmp_path):
    mb, _, _, gate = build(tmp_path)
    r = await gate(ProposedAction(type=T.FORWARD, email_id="e01", params={"to": ["accounting@acme.example"]}))
    assert r.outcome == Outcome.EXECUTED and mb.outbox[0]["to"] == ["accounting@acme.example"]


async def test_external_forward_waits_and_reject_leaves_outbox_empty(tmp_path):
    mb, audit, approvals, gate = build(tmp_path)
    a = ProposedAction(type=T.FORWARD, email_id="e01", params={"to": ["collect@evil-payments.example"]})
    task = asyncio.create_task(gate(a))
    for _ in range(100):
        if approvals.pending:
            break
        await asyncio.sleep(0.01)
    assert mb.outbox == []  # nothing sent while waiting
    approvals.resolve(a.id, False)
    r = await task
    assert r.outcome == Outcome.REJECTED_BY_HUMAN and mb.outbox == []


async def test_bad_params_become_failed_not_crash(tmp_path):
    mb, audit, _, gate = build(tmp_path)
    r = await gate(ProposedAction(type=T.LABEL, email_id="e01", params={}))
    assert r.outcome == Outcome.FAILED and "label" in r.detail
    assert len(audit.read_all()) == 1


async def test_unknown_email_fails_safe_to_human_then_times_out(tmp_path):
    mb, audit, _, gate = build(tmp_path, timeout=0.05)
    r = await gate(ProposedAction(type=T.LABEL, email_id="e99", params={"label": "x"}))
    assert r.decision.rule_id == "evaluation-error" and r.outcome == Outcome.REJECTED_BY_HUMAN
    assert len(audit.read_all()) == 1

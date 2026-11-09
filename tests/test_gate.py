import asyncio

import pytest

from app.approvals import ApprovalStore
from app.audit.log import AuditLog
from app.contracts import ActionType as T, Email, Outcome, ProposedAction, Verdict
from app.gate import make_gate
from app.policy.engine import PolicyEngine


class FakeMailbox:
    def __init__(self, fail=False):
        self.executed: list[ProposedAction] = []
        self.fail = fail
        self.emails = {"e1": Email(id="e1", sender="client@outside.example", to=["me@acme.example"],
                                   subject="s", body="b", date="2025-01-01T09:00:00")}

    def get(self, email_id):
        return self.emails[email_id]

    def execute(self, action):
        if self.fail:
            raise RuntimeError("disk on fire")
        self.executed.append(action)
        return f"did {action.type.value}"


BLOCK_RULES = """
rules:
  - {id: no-evil, verdict: block, reason: 'evil domain', when: {action_type: [forward], recipient: external, flags_all: [external_recipient]}}
"""


def setup(tmp_path, mailbox=None, rules="rules.yaml", timeout=5.0):
    mb = mailbox or FakeMailbox()
    audit = AuditLog(str(tmp_path / "a.jsonl"))
    approvals = ApprovalStore(timeout=timeout)
    return mb, audit, approvals, make_gate(PolicyEngine(rules), mb, audit, approvals)


def label():
    return ProposedAction(type=T.LABEL, email_id="e1", params={"label": "x"})


def ext_forward():
    return ProposedAction(type=T.FORWARD, email_id="e1", params={"to": ["x@outside.example"]})


async def wait_pending(approvals):
    for _ in range(100):
        if approvals.pending:
            return
        await asyncio.sleep(0.01)
    raise AssertionError("nothing became pending")


async def test_allow_executes_and_logs_once(tmp_path):
    mb, audit, _, gate = setup(tmp_path)
    r = await gate(label())
    assert r.outcome == Outcome.EXECUTED and len(mb.executed) == 1
    log = audit.read_all()
    assert len(log) == 1 and log[0].approval == "auto" and log[0].outcome == Outcome.EXECUTED


async def test_block_never_executes(tmp_path):
    rules = tmp_path / "r.yaml"
    rules.write_text(BLOCK_RULES)
    mb, audit, _, gate = setup(tmp_path, rules=str(rules))
    r = await gate(ext_forward())
    assert r.outcome == Outcome.BLOCKED and mb.executed == []
    assert "no-evil" in r.detail and "Do not retry" in r.detail
    log = audit.read_all()
    assert len(log) == 1 and log[0].decision.verdict == Verdict.BLOCK and log[0].approval is None


async def test_ask_waits_then_approve_executes(tmp_path):
    mb, audit, approvals, gate = setup(tmp_path)
    action = ext_forward()
    task = asyncio.create_task(gate(action))
    await wait_pending(approvals)
    assert mb.executed == [] and not task.done()  # nothing happens before the human answers
    assert approvals.resolve(action.id, True)
    r = await task
    assert r.outcome == Outcome.EXECUTED and len(mb.executed) == 1
    log = audit.read_all()
    assert len(log) == 1 and log[0].approval == "approved"


async def test_ask_reject_does_not_execute(tmp_path):
    mb, audit, approvals, gate = setup(tmp_path)
    action = ext_forward()
    task = asyncio.create_task(gate(action))
    await wait_pending(approvals)
    approvals.resolve(action.id, False)
    r = await task
    assert r.outcome == Outcome.REJECTED_BY_HUMAN and mb.executed == []
    log = audit.read_all()
    assert len(log) == 1 and log[0].approval == "rejected"


async def test_ask_timeout_counts_as_reject(tmp_path):
    mb, audit, _, gate = setup(tmp_path, timeout=0.05)
    r = await gate(ext_forward())
    assert r.outcome == Outcome.REJECTED_BY_HUMAN and mb.executed == []
    assert len(audit.read_all()) == 1


async def test_execution_failure_is_logged(tmp_path):
    mb, audit, _, gate = setup(tmp_path, mailbox=FakeMailbox(fail=True))
    r = await gate(label())
    assert r.outcome == Outcome.FAILED and "disk on fire" in r.detail
    assert audit.read_all()[0].outcome == Outcome.FAILED


async def test_policy_crash_fails_safe_to_ask(tmp_path):
    mb, audit, approvals, gate = setup(tmp_path, timeout=0.05)

    class Boom:
        def evaluate(self, *a, **k):
            raise RuntimeError("bug")

    gate = make_gate(Boom(), mb, audit, approvals)
    r = await gate(label())
    assert r.decision.rule_id == "policy-error" and r.outcome == Outcome.REJECTED_BY_HUMAN and mb.executed == []


async def test_events_and_history(tmp_path):
    mb, audit, approvals, gate = setup(tmp_path)
    seen = []
    approvals.subscribe(lambda e: seen.append(e["kind"]))
    await gate(label())
    assert seen == ["proposed", "done"]


async def test_one_audit_entry_per_action_across_many(tmp_path):
    mb, audit, approvals, gate = setup(tmp_path, timeout=0.02)
    for _ in range(3):
        await gate(label())
    await gate(ext_forward())  # times out -> rejected
    assert len(audit.read_all()) == 4

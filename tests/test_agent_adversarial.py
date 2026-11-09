"""Phase 4.A1-A3: trick variants end to end through the real gate, and the honest flow still works."""
import tempfile
from pathlib import Path

from app.agent.adversarial import load_variants, run_all
from app.agent.scripted import scripted_actions
from app.approvals import ApprovalStore
from app.audit.log import AuditLog
from app.contracts import Outcome, Verdict, is_internal
from app.gate import make_gate
from app.mailbox import Mailbox
from app.policy.engine import PolicyEngine


def test_variants_file_is_valid():
    assert len(load_variants()) >= 3


async def test_every_variant_gets_at_least_its_expected_verdict_and_nothing_leaks():
    rows = await run_all()
    assert all(r["ok"] for r in rows), [r for r in rows if not r["ok"]]
    assert all(r["sent"] == 0 for r in rows)


async def test_honest_flow_is_not_blocked_even_after_reading_the_trick():
    """4.A3: invoices to internal accounting run; outside replies reach the human; only the trick is blocked."""
    with tempfile.TemporaryDirectory() as tmp:
        mb = Mailbox()
        gate = make_gate(PolicyEngine(), mb, AuditLog(str(Path(tmp) / "a.jsonl")), ApprovalStore(timeout=0.01))
        results = [(a, await gate(a)) for a in scripted_actions()]
    for a, r in results:
        evil = a.type.value == "forward" and not is_internal(a.params["to"][0])
        if evil:
            assert r.decision.verdict is Verdict.BLOCK
        else:
            assert r.decision.verdict is not Verdict.BLOCK, (a.email_id, r.decision.rule_id)
    internal_forwards = [r for a, r in results if a.type.value == "forward" and is_internal(a.params["to"][0])]
    assert internal_forwards and all(r.outcome is Outcome.EXECUTED for r in internal_forwards)

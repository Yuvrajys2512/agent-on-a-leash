"""Phase 4.A1/4.A2: end-to-end trick variants through the REAL gate (policy + approvals + audit).

    python -m app.agent.adversarial       # prints the caught / missed table

Each variant replaces the trick email e09 in the inbox, then a compromised agent proposes the attack
(forward the invoices, or reply with bank details) after reading the whole inbox. Approvals are not answered,
so an ASK times out and counts as rejected. Verdicts: BLOCK = caught, ASK = reached the human, ALLOW = missed.
"""
from __future__ import annotations

import asyncio
import json
import tempfile
from pathlib import Path

from app.approvals import ApprovalStore
from app.audit.log import AuditLog
from app.contracts import ActionType, Email, ProposedAction
from app.gate import make_gate
from app.mailbox import INBOX, Mailbox
from app.policy.engine import PolicyEngine

VARIANTS = Path(__file__).resolve().parents[2] / "data" / "inbox_adversarial.json"
STRICTNESS = {"allow": 0, "ask": 1, "block": 2}


def load_variants(path: Path = VARIANTS) -> list[dict]:
    variants = json.loads(path.read_text(encoding="utf-8"))
    for v in variants:  # each trick email must be a valid Email once it takes e09's place
        Email.model_validate({**_base_e09(), **v["email"]})
    return variants


def _base_e09() -> dict:
    return next(e for e in json.loads(INBOX.read_text(encoding="utf-8")) if e["id"] == "e09")


def _attack_actions(v: dict, read: list[str]) -> list[ProposedAction]:
    a = v["attack"]
    why = f"[SIMULATED COMPROMISE] following the instruction in e09 ({v['id']})"
    if a["kind"] == "forward":
        return [ProposedAction(type=ActionType.FORWARD, email_id=e, params={"to": [a["to"]]},
                               rationale=why, context_email_ids=read) for e in a["emails"]]
    return [ProposedAction(type=ActionType.REPLY, email_id="e09", params={"body": a["body"]},
                           rationale=why, context_email_ids=read)]


async def run_variant(v: dict) -> dict:
    with tempfile.TemporaryDirectory() as tmp:
        inbox = [({**e, **v["email"]} if e["id"] == "e09" else e) for e in json.loads(INBOX.read_text(encoding="utf-8"))]
        path = Path(tmp) / "inbox.json"
        path.write_text(json.dumps(inbox), encoding="utf-8")
        mb = Mailbox(path)
        gate = make_gate(PolicyEngine(), mb, AuditLog(str(Path(tmp) / "audit.jsonl")), ApprovalStore(timeout=0.01))
        read = [e.id for e in mb.list()]
        results = [await gate(a) for a in _attack_actions(v, read)]
    worst = min((r.decision.verdict.value for r in results), key=STRICTNESS.get)  # the weakest verdict any step got
    return {"id": v["id"], "title": v["title"], "expect": v["expect"], "got": worst,
            "rules": sorted({r.decision.rule_id for r in results}), "sent": len(mb.outbox),
            "ok": STRICTNESS[worst] >= STRICTNESS[v["expect"]]}


async def run_all() -> list[dict]:
    return [await run_variant(v) for v in load_variants()]


if __name__ == "__main__":
    rows = asyncio.run(run_all())
    print(f"{'variant':<22} {'expected':<9} {'got':<6} {'sent':<5} result   rule(s)")
    for r in rows:
        mark = "caught" if r["got"] == "block" else "asked" if r["got"] == "ask" else "MISSED"
        print(f"{r['id']:<22} {r['expect']:<9} {r['got']:<6} {r['sent']:<5} {('OK ' if r['ok'] else 'GAP ') + mark:<8} {', '.join(r['rules'])}")
    gaps = [r for r in rows if not r["ok"]]
    print(f"\n{len(rows) - len(gaps)} of {len(rows)} variants got at least the expected verdict." + (" Gaps go to Person B." if gaps else ""))

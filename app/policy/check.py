"""CLI (Phase 1.B4): ask the policy engine what it would do with an action, without running anything.

    python -m app.policy.check '{"type":"forward","email_id":"e1","params":{"to":["x@evil.example"]}}'

Emails for REPLY lookups are read from data/inbox.json if it exists (override with --inbox).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from app.contracts import Email, ProposedAction
from app.policy.engine import PolicyEngine
from app.policy.rules import RulesError


class _JsonMailbox:
    """Read-only mailbox over an inbox.json file; only what the policy engine needs."""

    def __init__(self, path: str) -> None:
        p = Path(path)
        raw = json.loads(p.read_text(encoding="utf-8")) if p.exists() else []
        if isinstance(raw, dict):
            raw = raw.get("emails", [])
        self.emails = {e.id: e for e in (Email.model_validate(x) for x in raw)}

    def list(self) -> list[Email]:  # the engine uses this to learn the names of known people
        return list(self.emails.values())

    def get(self, email_id: str) -> Email:
        return self.emails[email_id]


def format_decision(action: ProposedAction, decision) -> str:
    lines = [
        f"Action : {action.type.value} on email {action.email_id} {json.dumps(action.params)}",
        f"Verdict: {decision.verdict.value.upper()}",
        f"Rule   : {decision.rule_id}",
        f"Reason : {decision.reason}",
        f"Flags  : {', '.join(decision.flags) or '-'}",
    ]
    others = [h for h in decision.hits if h.rule_id != decision.rule_id]
    if others:
        lines.append("Also matched: " + "; ".join(f"{h.rule_id} -> {h.verdict.value}" for h in others))
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m app.policy.check", description=__doc__.splitlines()[0])
    ap.add_argument("action_json", help="a ProposedAction as JSON (type, email_id, params)")
    ap.add_argument("--rules", default="rules.yaml")
    ap.add_argument("--inbox", default="data/inbox.json")
    args = ap.parse_args(argv)
    try:
        action = ProposedAction.model_validate_json(args.action_json)
        engine = PolicyEngine(args.rules)
        mailbox = _JsonMailbox(args.inbox)
    except (RulesError, ValueError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    print(format_decision(action, engine.evaluate(action, mailbox)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

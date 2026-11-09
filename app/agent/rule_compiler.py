"""Plain English -> rule (Phase 5.A1-A3). Person A writes the function, Person B adds the route and UI.

    proposal = await compile_rule("Ask me before moving any email")
    proposal.ok, proposal.rule, proposal.preview, proposal.problem

Safety, in code (not in the prompt):
- A compiled rule is only a PROPOSAL. Nothing here activates it; the human confirms first (5.A2).
- It may only TIGHTEN (verdict ask or block). An "allow" rule is refused, so no sentence can loosen the leash.
- merge_rules() puts every core BLOCK rule first, then the user's rules, then the rest of the core file, so a
  user rule can never turn a core block into an ask or allow, and still takes effect before the core allows.
- Anything invalid, unexpressible or unexpected -> ok=False with a plain reason, and nothing changes (5.A3).
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass

from pydantic import ValidationError

from app.contracts import ActionType, Verdict
from app.policy.rules import Rule

FLAG_FAMILIES = ["tainted", "injection_suspected", "untrusted_recipient", "bulk", "pii", "pii:iban", "pii:phone", "pii:name"]
FLAG_MEANING = {
    "tainted": "the agent has read mail from outside the company",
    "injection_suspected": "an email that can steer this send contains instructions aimed at an AI",
    "untrusted_recipient": "the outside address came from the text of an email, not from Sam",
    "bulk": "the 3rd or later forward of different emails to the same outside address",
    "pii": "what would be sent contains personal data (IBAN, phone number or a person's name)",
    "pii:iban": "what would be sent contains an IBAN", "pii:phone": "it contains a phone number",
    "pii:name": "it contains a known person's name",
}

_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["expressible", "problem", "rule"],
    "properties": {
        "expressible": {"type": "boolean"},
        "problem": {"type": "string"},
        "rule": {
            "type": "object", "additionalProperties": False,
            "required": ["id", "description", "action_type", "recipient", "flags_all", "verdict", "reason"],
            "properties": {
                "id": {"type": "string"},
                "description": {"type": "string"},
                "action_type": {"type": "array", "items": {"type": "string", "enum": [t.value for t in ActionType]}},
                "recipient": {"type": "string", "enum": ["any", "internal", "external"]},
                "flags_all": {"type": "array", "items": {"type": "string", "enum": FLAG_FAMILIES}},
                "verdict": {"type": "string", "enum": [v.value for v in Verdict]},
                "reason": {"type": "string"},
            },
        },
    },
}

_PROMPT = """Turn one email-safety rule, written by the user in plain English, into a rule for a rule engine.

The engine can only check these things about an action the email agent wants to take:
- action_type: any of label, move, reply, forward (empty list = any action)
- recipient: "internal" (inside the company), "external" (outside), or "any"
- flags_all: conditions that must ALL be true:
{flags}
- verdict: "ask" (the human must approve), "block" (never), or "allow"

It can NOT check words in the email, specific people or addresses, dates, amounts or attachments.
If the sentence needs anything it cannot check, set expressible=false and explain in "problem" in one short
plain sentence; still fill "rule" with your closest attempt. Otherwise expressible=true and problem="".
"id": short kebab-case. "description": the rule in one plain sentence. "reason": what the user sees when it fires.

The user's rule: {sentence!r}"""


@dataclass
class RuleProposal:
    ok: bool
    sentence: str
    rule: Rule | None = None
    preview: str = ""   # plain-language read-back for the human to confirm
    problem: str = ""   # why it can't be used (ok=False)


def preview(rule: Rule) -> str:
    acts = ", ".join(t.value for t in rule.when.action_type) or "any action"
    who = {"any": "", "internal": " to someone inside the company", "external": " to someone outside the company"}[rule.when.recipient]
    conds = "".join(f", when {FLAG_MEANING[f]}" for f in rule.when.flags_all)
    verdict = {"ask": "ask you first", "block": "block it"}[rule.verdict.value]
    return f"For {acts}{who}{conds}: {verdict}."


def check(sentence: str, data: dict) -> RuleProposal:
    """Deterministic gate on whatever the AI returned. Only this decides whether a rule is usable."""
    if not isinstance(data, dict) or not isinstance(data.get("rule"), dict):
        return RuleProposal(False, sentence, problem="The answer didn't contain a rule.")
    if data.get("expressible") is False:
        return RuleProposal(False, sentence, problem=data.get("problem") or "The rule engine can't check that.")
    r = data["rule"]
    unknown = [f for f in r.get("flags_all", []) if f not in FLAG_FAMILIES]
    if unknown:
        return RuleProposal(False, sentence, problem=f"Unknown condition(s): {', '.join(unknown)}.")
    slug = re.sub(r"[^a-z0-9-]+", "-", str(r.get("id", "")).lower()).strip("-") or "rule"
    try:
        rule = Rule.model_validate({
            "id": f"user-{slug}", "description": r.get("description") or sentence,
            "when": {"action_type": r.get("action_type", []), "recipient": r.get("recipient", "any"),
                     "flags_all": r.get("flags_all", [])},
            "verdict": r.get("verdict"), "reason": r.get("reason") or f"Your rule: {sentence}",
        })
    except ValidationError as e:
        return RuleProposal(False, sentence, problem=f"The rule wasn't valid: {e.errors()[0]['msg']}.")
    if rule.verdict is Verdict.ALLOW:
        return RuleProposal(False, sentence, rule=rule,
                            problem="Your rules can only make the leash tighter (ask or block), never looser.")
    return RuleProposal(True, sentence, rule=rule, preview=preview(rule))


async def compile_rule(sentence: str, *, client=None) -> RuleProposal:
    """Ask Claude to translate the sentence, then gate the result in code. Never activates anything."""
    sentence = (sentence or "").strip()
    if not sentence:
        return RuleProposal(False, sentence, problem="Type a rule first.")
    if client is None:
        from anthropic import AsyncAnthropic
        client = AsyncAnthropic()
    try:
        response = await client.messages.create(
            model=os.getenv("AGENT_MODEL", "claude-sonnet-5-5"), max_tokens=4000,
            messages=[{"role": "user", "content": _PROMPT.format(
                flags="\n".join(f"    {f}: {m}" for f, m in FLAG_MEANING.items()), sentence=sentence)}],
            output_config={"format": {"type": "json_schema", "schema": _SCHEMA}},
        )
        if response.stop_reason != "end_turn":
            return RuleProposal(False, sentence, problem=f"Claude didn't finish ({response.stop_reason}). Try again.")
        data = json.loads(next(b.text for b in response.content if b.type == "text"))
    except Exception as e:  # network, auth, refusal, bad JSON: fail safe, nothing changes
        return RuleProposal(False, sentence, problem=f"Couldn't write that rule ({type(e).__name__}). Nothing changed.")
    return check(sentence, data)


def merge_rules(core: list[Rule], user: list[Rule]) -> list[Rule]:
    """Core blocks first, then the user's (ask/block only), then the rest of the core file. Ids stay unique."""
    taken = {r.id for r in core}
    safe_user = [r for r in user if r.verdict is not Verdict.ALLOW and r.id not in taken]
    return ([r for r in core if r.verdict is Verdict.BLOCK] + safe_user
            + [r for r in core if r.verdict is not Verdict.BLOCK])


if __name__ == "__main__":  # python -m app.agent.rule_compiler "Ask me before moving any email" ...
    import asyncio
    import sys

    from dotenv import load_dotenv

    load_dotenv()

    async def main():
        from anthropic import AsyncAnthropic
        client = AsyncAnthropic()
        for s in sys.argv[1:]:
            p = await compile_rule(s, client=client)
            print(f"\n{s!r}\n  {'OK ' + p.preview if p.ok else 'REFUSED: ' + p.problem}")
            if p.rule:
                print("  ", p.rule.model_dump(mode="json"))

    asyncio.run(main())

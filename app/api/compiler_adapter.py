"""Connects Person A's `compile_rule(sentence)` (one checked rule, or a plain reason why not) to the API's
`rule_compiler(text) -> list[dict]` shape. Person B owns this file."""
from __future__ import annotations

from app.policy.user_rules import RuleRefused


async def compile_via_agent(text: str, *, client=None) -> list[dict]:
    from app.agent.rule_compiler import compile_rule  # imported here so the API starts even without the SDK

    proposal = await compile_rule(text, client=client)
    if not proposal.ok:
        raise RuleRefused(proposal.problem or "That rule could not be used.")
    return [proposal.rule.model_dump(mode="json")]

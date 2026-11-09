"""Rule file format + loader (Phase 1.B1). Owned by Person B."""
from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.contracts import ActionType, Verdict


class RulesError(Exception):
    """Raised when rules.yaml is missing or malformed. Message is meant for humans."""


class When(BaseModel):
    """Every condition that is given must hold. For the list conditions, any one entry matching is enough."""
    model_config = ConfigDict(extra="forbid")
    action_type: list[ActionType] = Field(default_factory=list)  # empty = any action
    recipient: Literal["any", "internal", "external"] = "any"
    flags_all: list[str] = Field(default_factory=list)           # all of these flags must be present
    recipient_domain: list[str] = Field(default_factory=list)    # a recipient is at one of these domains (or a subdomain)
    sender_domain: list[str] = Field(default_factory=list)       # the email being acted on came from one of these domains
    subject_contains: list[str] = Field(default_factory=list)    # the email's subject contains one of these words


class Rule(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    description: str = ""
    when: When = Field(default_factory=When)
    verdict: Verdict
    reason: str


class RuleSet(BaseModel):
    model_config = ConfigDict(extra="forbid")
    rules: list[Rule]


def load_rules(path: str | Path = "rules.yaml") -> RuleSet:
    p = Path(path)
    if not p.exists():
        raise RulesError(f"Rules file not found: {p}")
    try:
        data = yaml.safe_load(p.read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        raise RulesError(f"{p} is not valid YAML: {e}") from e
    if not isinstance(data, dict):
        raise RulesError(f"{p} must contain a top-level 'rules:' list")
    try:
        ruleset = RuleSet.model_validate(data)
    except ValidationError as e:
        problems = "; ".join(f"{'.'.join(map(str, err['loc']))}: {err['msg']}" for err in e.errors())
        raise RulesError(f"Invalid rules in {p}: {problems}") from e
    ids = [r.id for r in ruleset.rules]
    dupes = {i for i in ids if ids.count(i) > 1}
    if dupes:
        raise RulesError(f"Duplicate rule ids in {p}: {', '.join(sorted(dupes))}")
    return ruleset


# ---------- what a rule may refer to (used to check rules written by someone, or something, else) ----------
KNOWN_FLAGS = ("internal_recipient", "external_recipient", "tainted", "injection_suspected", "injection_in_context",
               "untrusted_recipient", "bulk", "pii", "pii:iban", "pii:phone", "pii:name")


def flag_is_known(flag: str) -> bool:
    return flag in KNOWN_FLAGS or any(flag.startswith(f + ":") for f in KNOWN_FLAGS)


def rules_format_help() -> str:
    """Plain-text description of the rule format. Handed to the AI that turns plain English into rules."""
    return """\
A rule is a YAML mapping:
  id:          short-kebab-case-name
  description: one plain sentence saying what the rule does
  verdict:     ask | block        (plain-English rules may NOT use allow)
  reason:      sentence shown to the user when the rule fires. May use {what}, {recipients}.
  when:        (every condition given must hold; omit the ones you do not need)
    action_type:      list of label | move | reply | forward
    recipient:        any | internal | external      (internal = inside the company)
    recipient_domain: list of domains, e.g. [competitor.example]   (a recipient is at one of them)
    sender_domain:    list of domains                              (the email being acted on came from one of them)
    subject_contains: list of words, e.g. [invoice]                (case-insensitive)
    flags_all:        list of flags that must all be present:
                      """ + ", ".join(KNOWN_FLAGS) + """

Examples
  "Never send anything outside the company without asking me":
    - id: ask-before-outside-send
      description: Ask before anything leaves the company.
      verdict: ask
      reason: "{what} would go outside the company ({recipients})."
      when: {action_type: [reply, forward], recipient: external}
  "Never forward invoices to anyone outside the company":
    - id: block-invoice-forward-outside
      description: Invoices must not leave the company.
      verdict: block
      reason: "Invoices must not be forwarded outside the company ({recipients})."
      when: {action_type: [forward], recipient: external, subject_contains: [invoice]}
  "Block everything to evil-payments.example":
    - id: block-evil-payments
      description: Nothing may go to evil-payments.example.
      verdict: block
      reason: "{recipients} is on the blocked list."
      when: {action_type: [reply, forward], recipient_domain: [evil-payments.example]}
"""

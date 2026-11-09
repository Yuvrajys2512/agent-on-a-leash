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
    model_config = ConfigDict(extra="forbid")
    action_type: list[ActionType] = Field(default_factory=list)  # empty = any action
    recipient: Literal["any", "internal", "external"] = "any"
    flags_all: list[str] = Field(default_factory=list)           # all of these flags must be present


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

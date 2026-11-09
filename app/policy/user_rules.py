"""Rules written in plain English (Phase 5.A, engine side). Owned by Person B.

Whatever turns English into rules (an AI, so untrusted) hands over a list of dicts. This module checks them
strictly, and `UserRulesStore` saves the ones a human has confirmed. Nothing here ever loosens the core rules:
the engine only lets user rules make an answer stricter (see engine.py).

What is refused, and why:
  verdict "allow"          could loosen the fail-safe default ("anything not covered asks the human")
  unknown flags            the rule would never fire, which gives a false sense of safety
  invalid / extra fields   the loader would reject it later, so say so now
  too many / too long      keeps the log and the rules panel readable
"""
from __future__ import annotations

import json
import re
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from app.contracts import Verdict
from app.policy.rules import Rule, RuleSet, RulesError, flag_is_known, load_rules

MAX_RULES = 20
PREFIX = "user-"
KNOWN_PLACEHOLDERS = {
    "what", "recipients", "tainted_by", "injection_email", "injection_subject", "injection_sender",
    "injection_phrase", "untrusted_email", "untrusted_sender", "bulk_count", "pii_kinds", "preview",
}
HEADER = ("# Rules added from plain English. A human confirmed each one.\n"
          "# They can only make the agent STRICTER (ask or block); they can never loosen rules.yaml.\n")


@dataclass
class Checked:
    rules: list[Rule] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)    # any error means nothing may be activated
    warnings: list[str] = field(default_factory=list)  # shown to the human, do not stop activation

    @property
    def ok(self) -> bool:
        return not self.errors and bool(self.rules)


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:50] or "rule"


def _clean_domain(d: str) -> str:
    return d.strip().lower().lstrip("@").removeprefix("*.").strip(".")


def check_proposed(raw: Any, taken_ids: set[str] = frozenset()) -> Checked:
    """Validate and normalise proposed rules. `taken_ids` are ids already in use (core and user rules)."""
    out = Checked()
    if not isinstance(raw, list) or not raw:
        out.errors.append("Expected a non-empty list of rules.")
        return out
    if len(raw) > MAX_RULES:
        out.errors.append(f"Too many rules ({len(raw)}); the limit is {MAX_RULES} at a time.")
        return out

    used = set(taken_ids)
    for n, item in enumerate(raw, 1):
        label = f"Rule {n}"
        if not isinstance(item, dict):
            out.errors.append(f"{label}: must be a mapping, got {type(item).__name__}.")
            continue
        item = dict(item)
        if isinstance(item.get("id"), str) and item["id"].strip():
            label = f"Rule {n} ({item['id']})"
        # Normalise the id: kebab-case, always starts with "user-", unique.
        rid = _slug(str(item.get("id") or item.get("description") or f"rule-{n}"))
        rid = rid if rid.startswith(PREFIX) else PREFIX + rid
        base, k = rid, 2
        while rid in used:
            rid, k = f"{base}-{k}", k + 1
        if rid != item.get("id"):
            out.warnings.append(f"{label}: id set to '{rid}'.")
        item["id"] = rid
        used.add(rid)

        try:
            rule = Rule.model_validate(item)
        except ValidationError as e:
            out.errors.append(f"{label}: " + "; ".join(f"{'.'.join(map(str, x['loc']))}: {x['msg']}" for x in e.errors()))
            continue

        if rule.verdict == Verdict.ALLOW:
            out.errors.append(f"{label}: plain-English rules may only 'ask' or 'block'. An 'allow' rule could loosen "
                              f"the safe default, so it is refused.")
            continue
        unknown = [f for f in rule.when.flags_all if not flag_is_known(f)]
        if unknown:
            out.errors.append(f"{label}: unknown flag(s) {', '.join(unknown)}; such a rule would never fire.")
            continue
        if len(rule.reason) > 300 or len(rule.description) > 200:
            out.errors.append(f"{label}: reason must be at most 300 characters and description at most 200.")
            continue
        if not rule.reason.strip():
            out.errors.append(f"{label}: needs a reason, shown to you when the rule fires.")
            continue

        w = rule.when
        w.recipient_domain = [d for d in map(_clean_domain, w.recipient_domain) if d]
        w.sender_domain = [d for d in map(_clean_domain, w.sender_domain) if d]
        w.subject_contains = [s.strip() for s in w.subject_contains if s.strip()]
        if any("." not in d for d in w.recipient_domain + w.sender_domain):
            out.warnings.append(f"{label}: a domain without a dot (e.g. 'example') probably matches nothing.")
        if not (w.action_type or w.recipient != "any" or w.recipient_domain or w.sender_domain
                or w.subject_contains or w.flags_all):
            out.warnings.append(f"{label}: has no conditions, so it applies to EVERY action.")
        bad = set(re.findall(r"\{(\w+)\}", rule.reason)) - KNOWN_PLACEHOLDERS
        if bad:
            out.warnings.append(f"{label}: unknown placeholder(s) {', '.join('{'+b+'}' for b in sorted(bad))} "
                                f"will show as written.")
        out.rules.append(rule)
    return out


def rules_to_yaml(rules: list[Rule]) -> str:
    data = {"rules": [r.model_dump(mode="json", exclude_defaults=True) for r in rules]}
    return HEADER + yaml.safe_dump(data, sort_keys=False, allow_unicode=True, width=100)


class UserRulesStore:
    """The second rules file plus a history of who/what changed it. Writes are atomic."""

    def __init__(self, path: str = "rules.user.yaml") -> None:
        self.path = Path(path)
        self.history_path = self.path.with_name(self.path.stem + ".history.jsonl")
        self._lock = threading.Lock()

    def load(self) -> list[Rule]:
        return load_rules(self.path).rules if self.path.exists() else []

    def ids(self) -> set[str]:
        return {r.id for r in self.load()}

    def _write(self, rules: list[Rule]) -> None:
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(rules_to_yaml(rules), encoding="utf-8")
        tmp.replace(self.path)

    def _log(self, what: str, **info: Any) -> None:
        line = json.dumps({"ts": datetime.now(timezone.utc).isoformat(), "what": what, **info}, ensure_ascii=False)
        with self.history_path.open("a", encoding="utf-8") as f:
            f.write(line + "\n")

    def add(self, rules: list[Rule], english: str = "") -> list[Rule]:
        """Save confirmed rules (already checked). Re-checks so a stale proposal cannot slip anything past."""
        with self._lock:
            current = self.load()
            checked = check_proposed([r.model_dump(mode="json") for r in rules], {r.id for r in current})
            if not checked.ok:
                raise RulesError("; ".join(checked.errors) or "No rules to add.")
            if len(current) + len(checked.rules) > MAX_RULES * 2:
                raise RulesError("Too many user rules in total; remove some first.")
            self._write(current + checked.rules)
            self._log("added", english=english, rules=[r.id for r in checked.rules])
            return checked.rules

    def remove(self, rule_id: str) -> bool:
        with self._lock:
            current = self.load()
            kept = [r for r in current if r.id != rule_id]
            if len(kept) == len(current):
                return False
            self._write(kept) if kept else self.path.unlink()
            self._log("removed", rule=rule_id)
            return True

    def archive(self) -> Path | None:
        """Set the user rules aside (used by Reset). The file is renamed, never deleted."""
        with self._lock:
            if not self.path.exists():
                return None
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
            target = self.path.with_name(f"{self.path.stem}-{stamp}{self.path.suffix}")
            self.path.rename(target)
            self._log("archived", to=target.name)
            return target


class RuleRefused(Exception):
    """The compiler understood the sentence but the rule cannot be used. The message is for the human."""


_ACT = {"label": "label an email", "move": "move an email", "reply": "reply", "forward": "forward"}


def describe_rule(rule: Rule) -> str:
    """One plain sentence read-back of a rule, shown to the human before they confirm it."""
    w = rule.when
    what = " or ".join(_ACT.get(a.value, a.value) for a in w.action_type) or "do anything"
    parts = [f"When the agent tries to {what}"]
    if w.recipient != "any":
        parts.append({"internal": "to someone inside the company", "external": "to someone outside the company"}[w.recipient])
    if w.recipient_domain:
        parts.append("to " + " or ".join(w.recipient_domain))
    if w.sender_domain:
        parts.append("about mail from " + " or ".join(w.sender_domain))
    if w.subject_contains:
        parts.append("with " + " or ".join(f"\"{s}\"" for s in w.subject_contains) + " in the subject")
    if w.flags_all:
        parts.append("and " + " and ".join(w.flags_all) + " is detected")
    outcome = {Verdict.ASK: "ask you first", Verdict.BLOCK: "block it", Verdict.ALLOW: "allow it"}[rule.verdict]
    return " ".join(parts) + f": {outcome}."

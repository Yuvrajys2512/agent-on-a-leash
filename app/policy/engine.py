"""Policy engine (Phase 1.B2, extended in Phase 4). Owned by Person B.

First matching rule wins, every matching rule is reported in Decision.hits.
If nothing matches, or anything goes wrong, the answer is ASK (fail safe).

Flags computed for each action (rules match on them with `flags_all`):
  internal_recipient / external_recipient   where the message would go (any external address = external)
  tainted                                   the agent has read mail from outside the company
  injection_suspected:<email id>            an email that can steer this send contains instructions aimed at an AI
                                            (forward: any email read; reply: only the email being answered)
  injection_in_context:<email id>           same, but for a reply to someone else: informational only
  untrusted_recipient:<email id>            the external recipient's address came from that email's text
  bulk                                      3rd+ forward of different emails to the same external address
  pii / pii:<kind>                          what would be sent contains an IBAN, phone number or a known person's name
A rule flag without a colon (e.g. `injection_suspected`) matches any flag of that family.

Rule reasons may contain {placeholders} (see `details` below); they are filled in per action.
"""
from __future__ import annotations

from app.contracts import (
    ActionType, AuditEntry, Decision, Email, MailboxProtocol, ProposedAction, RuleHit, Verdict, is_internal,
)
from app.policy.injection import addresses_in, scan, verdict as injection_verdict
from app.policy.pii import KIND_LABEL, find_pii, names_from_addresses, redact
from app.policy.rules import Rule, load_rules

DEFAULT_RULE_ID = "default-ask"
BULK_THRESHOLD = 3  # this forward + 2 earlier ones to the same external address


class _Safe(dict):
    def __missing__(self, key: str) -> str:  # an unknown {placeholder} stays visible instead of crashing
        return "{" + key + "}"


def _has(flag: str, flags: list[str]) -> bool:
    return flag in flags or any(f.startswith(flag + ":") for f in flags)


def _recipients_from_params(action: ProposedAction) -> list[str]:
    to = action.params.get("to") or []
    return [to] if isinstance(to, str) else list(to)


def _clip(text: str, n: int = 220) -> str:
    text = " ".join(text.split())
    return text if len(text) <= n else text[:n] + "..."


class PolicyEngine:
    def __init__(self, rules_path: str = "rules.yaml") -> None:
        self.rules_path = rules_path
        self.rules: list[Rule] = load_rules(rules_path).rules

    def recipients_of(self, action: ProposedAction, mailbox: MailboxProtocol) -> list[str] | None:
        """Everyone who would receive this. None if the action sends nothing."""
        if action.type == ActionType.FORWARD:
            return _recipients_from_params(action)
        if action.type == ActionType.REPLY:
            return [mailbox.get(action.email_id).sender]
        return None

    @staticmethod
    def outgoing_text(action: ProposedAction, mailbox: MailboxProtocol) -> str:
        """The text that would actually leave: the agent's own words, plus the quoted original for a forward."""
        text = str(action.params.get("body") or "")
        if action.type == ActionType.FORWARD:
            e = mailbox.get(action.email_id)
            text += f"\n\n{e.subject}\n{e.body}"
        return text

    @staticmethod
    def known_names(mailbox: MailboxProtocol) -> set[str]:
        lister = getattr(mailbox, "list", None)  # small test mailboxes may not have list()
        emails = lister() if callable(lister) else []
        return names_from_addresses(a for e in emails for a in [e.sender, *e.to])

    def analyze(self, action: ProposedAction, mailbox: MailboxProtocol,
                history: list[AuditEntry] | None = None) -> tuple[list[str], dict[str, str]]:
        """Returns (flags, details). `details` fills the {placeholders} in rule reasons."""
        flags: list[str] = []
        subject = mailbox.get(action.email_id).subject
        details: dict[str, str] = {"what": f"{action.type.value} email {action.email_id} (\"{subject}\")"}

        recipients = self.recipients_of(action, mailbox)
        external: list[str] = []
        if recipients is not None:
            # No recipients at all counts as external (fail safe). ONE external recipient makes it external.
            if recipients and all(is_internal(r) for r in recipients):
                flags.append("internal_recipient")
            else:
                flags.append("external_recipient")
            external = [r.lower() for r in recipients if not is_internal(r)]
            details["recipients"] = ", ".join(recipients) or "(nobody)"

        # Personal data in what would leave. A person's own name going back to them is not a leak.
        if recipients is not None:
            text = self.outgoing_text(action, mailbox)
            own = names_from_addresses(recipients)
            hits = find_pii(text, self.known_names(mailbox) - own)
            if hits:
                kinds = list(dict.fromkeys(h.kind for h in hits))
                flags.append("pii")
                flags.extend(f"pii:{k}" for k in kinds)
                details["pii_kinds"] = ", ".join(KIND_LABEL[k] for k in kinds)
                details["preview"] = _clip(redact(text, hits))

        # Everything the agent has read, plus the email it is acting on.
        ids = list(dict.fromkeys([*action.context_email_ids, action.email_id]))
        context: list[Email] = [mailbox.get(i) for i in ids]  # unknown id -> exception -> ASK (fail safe)
        outside = [e for e in context if not is_internal(e.sender)]
        if outside:
            flags.append("tainted")
            details["tainted_by"] = ", ".join(e.id for e in outside)

        # A reply always goes to the sender of the email being answered, so injected text elsewhere cannot
        # redirect it: for replies only that email counts. For forwards the agent picks the destination,
        # so anything it has read can have steered it.
        scope = [mailbox.get(action.email_id)] if action.type == ActionType.REPLY else context
        for e in context:
            hit = injection_verdict(scan(f"{e.subject}\n{e.body}"))
            if hit and e not in scope:
                flags.append(f"injection_in_context:{e.id}")  # informational: shown in the log, no rule blocks on it
            elif hit:
                flags.append(f"injection_suspected:{e.id}")
                details.setdefault("injection_email", e.id)
                details.setdefault("injection_subject", e.subject)
                details.setdefault("injection_sender", e.sender)
                details.setdefault("injection_phrase", hit.phrase)

        # Did the destination address come from the text of an outside email instead of from the user?
        # Checked against EVERY outside email in the mailbox, not just the ones the agent says it read,
        # so an agent that forgets (or hides) what it read does not blind this check.
        lister = getattr(mailbox, "list", None)
        pool = lister() if callable(lister) else context
        for e in (x for x in pool if not is_internal(x.sender)):
            mentioned = addresses_in(f"{e.subject}\n{e.body}") - {e.sender.lower()}
            if any(r in mentioned for r in external):
                flags.append(f"untrusted_recipient:{e.id}")
                details.setdefault("untrusted_email", e.id)
                details.setdefault("untrusted_sender", e.sender)

        if action.type == ActionType.FORWARD and external:
            earlier = {h.action.email_id for h in (history or [])
                       if h.action.type == ActionType.FORWARD and h.action.email_id != action.email_id
                       and set(map(str.lower, _recipients_from_params(h.action))) & set(external)}
            if len(earlier) + 1 >= BULK_THRESHOLD:
                flags.append("bulk")
                details["bulk_count"] = str(len(earlier) + 1)
        return flags, details

    @staticmethod
    def _matches(rule: Rule, action: ProposedAction, flags: list[str]) -> bool:
        w = rule.when
        if w.action_type and action.type not in w.action_type:
            return False
        if w.recipient == "internal" and "internal_recipient" not in flags:
            return False
        if w.recipient == "external" and "external_recipient" not in flags:
            return False
        return all(_has(f, flags) for f in w.flags_all)

    @staticmethod
    def _reason(rule: Rule, details: dict[str, str]) -> str:
        try:
            return rule.reason.format_map(_Safe(details))
        except (ValueError, IndexError, AttributeError):  # stray braces in a hand-written rule
            return rule.reason

    def evaluate(self, action: ProposedAction, mailbox: MailboxProtocol,
                 history: list[AuditEntry] | None = None) -> Decision:
        try:
            flags, details = self.analyze(action, mailbox, history)
        except Exception as e:  # e.g. unknown email id -> never allow silently
            reason = f"Could not evaluate action safely ({e}); asking the human."
            return Decision(verdict=Verdict.ASK, rule_id="evaluation-error", reason=reason, flags=["error"],
                            hits=[RuleHit(rule_id="evaluation-error", verdict=Verdict.ASK, reason=reason)])
        hits = [RuleHit(rule_id=r.id, verdict=r.verdict, reason=self._reason(r, details))
                for r in self.rules if self._matches(r, action, flags)]
        if hits:
            first = hits[0]  # first match decides; the rest are kept so the log can show every reason
            return Decision(verdict=first.verdict, rule_id=first.rule_id, reason=first.reason, flags=flags, hits=hits)
        reason = "No rule covers this action, so the human is asked."
        return Decision(verdict=Verdict.ASK, rule_id=DEFAULT_RULE_ID, reason=reason, flags=flags,
                        hits=[RuleHit(rule_id=DEFAULT_RULE_ID, verdict=Verdict.ASK, reason=reason)])

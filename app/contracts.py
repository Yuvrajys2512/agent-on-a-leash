"""Shared contracts between Person A (agent + mailbox) and Person B (gate + policy + audit).

FROZEN after Phase 0: any change needs a PR approved by both people.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from enum import Enum
from typing import Awaitable, Callable, Literal
from uuid import uuid4

from pydantic import BaseModel, Field

# "Company domain": addresses at this domain are internal, everything else is external.
COMPANY_DOMAIN = os.getenv("COMPANY_DOMAIN", "acme.example")


def is_internal(address: str) -> bool:
    return address.strip().lower().rsplit("@", 1)[-1] == COMPANY_DOMAIN


class Email(BaseModel):
    id: str
    sender: str
    to: list[str]
    subject: str
    body: str
    date: datetime
    folder: str = "inbox"
    labels: list[str] = Field(default_factory=list)


class ActionType(str, Enum):
    LABEL = "label"
    MOVE = "move"
    REPLY = "reply"
    FORWARD = "forward"


class ProposedAction(BaseModel):
    id: str = Field(default_factory=lambda: uuid4().hex[:8])
    type: ActionType
    email_id: str
    # LABEL: {"label"}; MOVE: {"folder"}; REPLY: {"body"}; FORWARD: {"to": list[str], "body"?}
    params: dict = Field(default_factory=dict)
    rationale: str = ""  # the agent's own explanation
    context_email_ids: list[str] = Field(default_factory=list)  # every email read before proposing (taint tracking)


class Verdict(str, Enum):
    ALLOW = "allow"
    ASK = "ask"
    BLOCK = "block"


class Decision(BaseModel):
    verdict: Verdict
    rule_id: str
    reason: str
    flags: list[str] = Field(default_factory=list)  # e.g. "external_recipient", "tainted", "pii:iban"


class Outcome(str, Enum):
    EXECUTED = "executed"
    REJECTED_BY_HUMAN = "rejected_by_human"
    BLOCKED = "blocked"
    FAILED = "failed"


class GateResult(BaseModel):
    outcome: Outcome
    decision: Decision
    detail: str = ""


class AuditEntry(BaseModel):
    ts: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    action: ProposedAction
    decision: Decision
    approval: Literal["auto", "approved", "rejected"] | None = None
    outcome: Outcome
    detail: str = ""


Gate = Callable[[ProposedAction], Awaitable[GateResult]]
# Person A implements:  async run_agent(mailbox, gate, task: str) -> None
# Person B implements:  PolicyEngine.evaluate(action, mailbox) -> Decision
#                       AuditLog.record(entry), AuditLog.read_all() -> list[AuditEntry]
#                       make_gate(policy, mailbox, audit, approvals) -> Gate

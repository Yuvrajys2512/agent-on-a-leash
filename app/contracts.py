"""SHARED CONTRACT between Person A (mailbox/agent) and Person B (policy/audit/gate/api/ui).

Changing anything here needs a PR approved by BOTH people.
"""
from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Awaitable, Callable, Literal, Optional, Protocol

from pydantic import BaseModel, Field

COMPANY_DOMAIN = os.getenv("COMPANY_DOMAIN", "acme.example")


def is_internal(address: str) -> bool:
    """True if the address belongs to the company domain. Everything else is external."""
    return address.strip().lower().rsplit("@", 1)[-1] == COMPANY_DOMAIN


def _new_id() -> str:
    return uuid.uuid4().hex[:8]


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ---------- Mailbox data ----------
class Email(BaseModel):
    id: str
    sender: str
    to: list[str]
    subject: str
    body: str
    date: str  # ISO 8601 string
    folder: str = "inbox"
    labels: list[str] = Field(default_factory=list)


# ---------- Actions the agent wants to take ----------
class ActionType(str, Enum):
    LABEL = "label"      # params: {"label": str}
    MOVE = "move"        # params: {"folder": str}
    REPLY = "reply"      # params: {"body": str}              (recipient = original sender)
    FORWARD = "forward"  # params: {"to": str, "body": str}


class ProposedAction(BaseModel):
    id: str = Field(default_factory=_new_id)
    type: ActionType
    email_id: str                      # the email being acted on
    params: dict[str, Any] = Field(default_factory=dict)
    rationale: str = ""                # agent's own explanation
    context_email_ids: list[str] = Field(default_factory=list)  # every email the agent had read so far (for taint tracking)


# ---------- Policy decision ----------
class Verdict(str, Enum):
    ALLOW = "allow"
    ASK = "ask"
    BLOCK = "block"


class Decision(BaseModel):
    verdict: Verdict
    rule_id: str
    reason: str                        # plain-English, shown in UI and log
    flags: list[str] = Field(default_factory=list)  # e.g. "external_recipient", "tainted", "pii:iban"


# ---------- Result of passing through the gate ----------
class Outcome(str, Enum):
    EXECUTED = "executed"
    REJECTED_BY_HUMAN = "rejected_by_human"
    BLOCKED = "blocked"
    FAILED = "failed"


class GateResult(BaseModel):
    outcome: Outcome
    decision: Decision
    detail: str = ""                   # fed back to the LLM as the tool result


# ---------- Audit log ----------
class AuditEntry(BaseModel):
    ts: datetime = Field(default_factory=_now)
    action: ProposedAction
    decision: Decision
    approval: Optional[Literal["auto", "approved", "rejected"]] = None
    outcome: Outcome
    detail: str = ""


# ---------- Interfaces ----------
# The agent never executes anything: it calls a Gate. Person B builds the gate.
Gate = Callable[[ProposedAction], Awaitable[GateResult]]


class MailboxProtocol(Protocol):
    """Implemented by Person A (app/mailbox). Person B's policy/gate code types against this."""

    def list(self, folder: Optional[str] = None) -> list[Email]: ...
    def get(self, email_id: str) -> Email: ...
    def execute(self, action: ProposedAction) -> str: ...  # performs the action on mock state, returns a description
    def reset(self) -> None: ...

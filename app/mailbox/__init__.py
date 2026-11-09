"""Person A: mock inbox and email tools. No network, ever."""
from __future__ import annotations

import copy
import json
from pathlib import Path

from app.contracts import ActionType, Email, ProposedAction

INBOX = Path(__file__).resolve().parents[2] / "data" / "inbox.json"


def load(path: str | Path = INBOX) -> list[Email]:
    """Read the inbox file and check every email against the Email contract."""
    return [Email.model_validate(e) for e in json.loads(Path(path).read_text(encoding="utf-8"))]


class Mailbox:
    """The inbox held in memory. Nothing here touches real email."""

    def __init__(self, path: str | Path = INBOX) -> None:
        self.path = Path(path)
        self.emails: dict[str, Email] = {e.id: e for e in load(self.path)}
        self.outbox: list[dict] = []

    def list(self, folder: str | None = None) -> list[Email]:
        return [e for e in self.emails.values() if folder is None or e.folder == folder]

    def get(self, email_id: str) -> Email:
        try:
            return self.emails[email_id]
        except KeyError:
            raise KeyError(f"No email with id {email_id!r} in the mailbox") from None

    # ---- Tools. They only change in-memory state and the outbox list. The gate calls execute(). ----

    def label(self, email_id: str, label: str) -> str:
        e = self.get(email_id)
        if label not in e.labels:
            e.labels.append(label)
        return f"Labelled {email_id} as {label!r}"

    def move(self, email_id: str, folder: str) -> str:
        e = self.get(email_id)
        e.folder = folder
        return f"Moved {email_id} to {folder!r}"

    def reply(self, email_id: str, body: str) -> str:
        e = self.get(email_id)
        self.outbox.append({"kind": "reply", "email_id": email_id, "to": [e.sender],
                            "subject": _prefixed("Re: ", e.subject), "body": body})
        return f"Reply to {e.sender} placed in the outbox (not really sent)"

    def forward(self, email_id: str, to: list[str] | str, body: str = "") -> str:
        e = self.get(email_id)
        to = [to] if isinstance(to, str) else list(to)
        if not to:
            raise ValueError("forward needs at least one address in 'to'")
        quoted = f"---------- Forwarded message ----------\nFrom: {e.sender}\nSubject: {e.subject}\n\n{e.body}"
        self.outbox.append({"kind": "forward", "email_id": email_id, "to": to,
                            "subject": _prefixed("Fwd: ", e.subject),
                            "body": f"{body}\n\n{quoted}" if body else quoted})
        return f"Forward of {email_id} to {', '.join(to)} placed in the outbox (not really sent)"

    def execute(self, action: ProposedAction) -> str:
        """Run one action that the gate has already allowed. Returns a short description of what changed."""
        p = action.params
        missing = [k for k in _REQUIRED[action.type] if k not in p]
        if missing:
            raise ValueError(f"{action.type.value} needs the parameter(s): {', '.join(missing)}")
        if action.type is ActionType.LABEL:
            return self.label(action.email_id, p["label"])
        if action.type is ActionType.MOVE:
            return self.move(action.email_id, p["folder"])
        if action.type is ActionType.REPLY:
            return self.reply(action.email_id, p["body"])
        return self.forward(action.email_id, p["to"], p.get("body", ""))

    # ---- Repeatable demos ----

    def snapshot(self) -> dict:
        """A copy of the whole state (emails and outbox) that later changes cannot touch."""
        return {"emails": [e.model_dump(mode="json") for e in self.emails.values()],
                "outbox": copy.deepcopy(self.outbox)}

    def reset(self) -> None:
        """Back to the original inbox from the file, with an empty outbox."""
        self.emails = {e.id: e for e in load(self.path)}
        self.outbox = []


_REQUIRED = {ActionType.LABEL: ["label"], ActionType.MOVE: ["folder"],
             ActionType.REPLY: ["body"], ActionType.FORWARD: ["to"]}


def _prefixed(prefix: str, subject: str) -> str:
    return subject if subject.lower().startswith(prefix.lower()) else prefix + subject

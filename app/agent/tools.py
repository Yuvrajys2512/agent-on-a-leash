"""Tools offered to the AI (Phase 2.A2) and how its tool calls become ProposedActions.

Read-only tools (list_emails, read_email) are answered here and mark emails as read.
Action tools never run: they become a ProposedAction that carries every email read so far.
"""
from __future__ import annotations

from app.contracts import ActionType, ProposedAction
from app.mailbox import Mailbox

_ID = {"email_id": {"type": "string", "description": "Id of the email, e.g. e01"}}

TOOLS: list[dict] = [
    {"name": "list_emails", "description": "List the emails in the inbox: id, sender, subject.",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "read_email", "description": "Read the full text of one email.",
     "input_schema": {"type": "object", "properties": _ID, "required": ["email_id"]}},
    {"name": "label_email", "description": "Add a label to an email.",
     "input_schema": {"type": "object", "properties": {**_ID, "label": {"type": "string"}},
                      "required": ["email_id", "label"]}},
    {"name": "move_email", "description": "Move an email to a folder.",
     "input_schema": {"type": "object", "properties": {**_ID, "folder": {"type": "string"}},
                      "required": ["email_id", "folder"]}},
    {"name": "reply_email", "description": "Reply to the sender of an email.",
     "input_schema": {"type": "object", "properties": {**_ID, "body": {"type": "string"},
                                                       "rationale": {"type": "string", "description": "Why, in one sentence"}},
                      "required": ["email_id", "body"]}},
    {"name": "forward_email", "description": "Forward an email to one or more addresses.",
     "input_schema": {"type": "object", "properties": {**_ID, "to": {"type": "array", "items": {"type": "string"}},
                                                       "body": {"type": "string"},
                                                       "rationale": {"type": "string", "description": "Why, in one sentence"}},
                      "required": ["email_id", "to"]}},
]
for _t in TOOLS[2:4]:  # label/move also take an optional rationale
    _t["input_schema"]["properties"]["rationale"] = {"type": "string", "description": "Why, in one sentence"}

ACTION_TOOLS = {"label_email": ActionType.LABEL, "move_email": ActionType.MOVE,
                "reply_email": ActionType.REPLY, "forward_email": ActionType.FORWARD}
READ_TOOLS = {"list_emails", "read_email"}


class ToolBox:
    """One run's view of the mailbox: answers reads, turns action calls into proposals."""

    def __init__(self, mailbox: Mailbox) -> None:
        self.mailbox = mailbox
        self.read: list[str] = []  # every email the agent has read, in order

    def read_only(self, name: str, args: dict) -> dict | list:
        if name == "list_emails":
            return [{"id": e.id, "sender": e.sender, "subject": e.subject, "folder": e.folder}
                    for e in self.mailbox.list()]
        if name == "read_email":
            e = self.mailbox.get(args["email_id"])
            if e.id not in self.read:
                self.read.append(e.id)
            return {"id": e.id, "sender": e.sender, "to": e.to, "subject": e.subject,
                    "date": e.date.isoformat(), "body": e.body}
        raise ValueError(f"{name} is not a read-only tool")

    def to_action(self, name: str, args: dict) -> ProposedAction:
        if name not in ACTION_TOOLS:
            raise ValueError(f"Unknown tool {name!r}")
        args = dict(args)
        email_id = args.pop("email_id")
        rationale = args.pop("rationale", "")
        if name == "forward_email" and isinstance(args.get("to"), str):
            args["to"] = [args["to"]]
        return ProposedAction(type=ACTION_TOOLS[name], email_id=email_id, params=args,
                              rationale=rationale, context_email_ids=list(self.read))

"""Person A: mock inbox and email tools. No network, ever. Phase 0 stub; filled in Phase 1."""
from __future__ import annotations

from app.contracts import Email, ProposedAction


class Mailbox:
    def __init__(self, path: str = "data/inbox.json") -> None:
        self.path = path
        self.emails: dict[str, Email] = {}
        self.outbox: list[dict] = []

    def list(self) -> list[Email]:
        return list(self.emails.values())

    def get(self, email_id: str) -> Email:
        return self.emails[email_id]

    def execute(self, action: ProposedAction) -> str:
        raise NotImplementedError("Phase 1.A3")

    def snapshot(self) -> dict:
        raise NotImplementedError("Phase 1.A4")

    def reset(self) -> None:
        raise NotImplementedError("Phase 1.A4")

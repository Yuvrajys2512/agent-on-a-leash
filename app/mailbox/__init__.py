"""Person A: mock inbox and email tools. No network, ever."""
from __future__ import annotations

import json
from pathlib import Path

from app.contracts import Email, ProposedAction

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

    def execute(self, action: ProposedAction) -> str:
        raise NotImplementedError("Phase 1.A3")

    def snapshot(self) -> dict:
        raise NotImplementedError("Phase 1.A4")

    def reset(self) -> None:
        raise NotImplementedError("Phase 1.A4")

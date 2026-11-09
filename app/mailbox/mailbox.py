"""STUB (Phase 0). Person A implements in Phase 1."""
from typing import Optional

from app.contracts import Email, ProposedAction


class Mailbox:
    def list(self, folder: Optional[str] = None) -> list[Email]:
        raise NotImplementedError

    def get(self, email_id: str) -> Email:
        raise NotImplementedError

    def execute(self, action: ProposedAction) -> str:
        raise NotImplementedError

    def reset(self) -> None:
        raise NotImplementedError

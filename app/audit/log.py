"""STUB (Phase 0). Person B implements in Phase 1."""
from __future__ import annotations

from app.contracts import AuditEntry


class AuditLog:
    def __init__(self, path: str = "audit.jsonl") -> None:
        self.path = path

    def record(self, entry: AuditEntry) -> None:
        raise NotImplementedError("Phase 1.B3")

    def read_all(self) -> list[AuditEntry]:
        raise NotImplementedError("Phase 1.B3")

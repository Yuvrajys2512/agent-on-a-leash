"""STUB (Phase 0). Person B implements in Phase 1."""
from app.contracts import AuditEntry


class AuditLog:
    def __init__(self, path: str = "audit.jsonl"):
        self.path = path

    def record(self, entry: AuditEntry) -> None:
        raise NotImplementedError

    def read_all(self) -> list[AuditEntry]:
        raise NotImplementedError

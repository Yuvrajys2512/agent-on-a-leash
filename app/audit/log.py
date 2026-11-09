"""Audit log (Phase 1.B3). Owned by Person B.

Append-only JSONL: one AuditEntry per line, never rewritten.
"""
from __future__ import annotations

import threading
from datetime import datetime
from pathlib import Path

from app.contracts import ActionType, AuditEntry, is_internal


class AuditLog:
    def __init__(self, path: str = "audit.jsonl") -> None:
        self.path = Path(path)
        self._lock = threading.Lock()

    def record(self, entry: AuditEntry) -> None:
        line = entry.model_dump_json()
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as f:  # "a" = append only
                f.write(line + "\n")

    def rotate(self) -> Path | None:
        """Start a fresh log for a new demo run. The old file is kept (renamed), never deleted."""
        with self._lock:
            if not self.path.exists():
                return None
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
            target = self.path.with_name(f"{self.path.stem}-{stamp}{self.path.suffix}")
            self.path.rename(target)
            return target

    def read_all(self) -> list[AuditEntry]:
        if not self.path.exists():
            return []
        with self._lock:
            lines = self.path.read_text(encoding="utf-8").splitlines()
        return [AuditEntry.model_validate_json(l) for l in lines if l.strip()]


def explain(entry: AuditEntry) -> str:
    """One human-readable paragraph saying what the agent tried, what the rules said, and what happened."""
    a, d = entry.action, entry.decision
    what = f"{a.type.value} on email {a.email_id}"
    if a.type == ActionType.FORWARD:
        to = a.params.get("to") or []
        to = [to] if isinstance(to, str) else to
        where = ", ".join(f"{r} ({'internal' if is_internal(r) else 'external'})" for r in to) or "no recipient"
        what += f" to {where}"
    elif a.type == ActionType.LABEL:
        what += f" with label '{a.params.get('label')}'"
    elif a.type == ActionType.MOVE:
        what += f" to folder '{a.params.get('folder')}'"
    text = f"Agent tried: {what}. Rule '{d.rule_id}' said {d.verdict.value.upper()}: {d.reason}"
    extra = [h for h in d.hits if h.rule_id != d.rule_id]
    if extra:
        text += " Also matched: " + "; ".join(f"{h.rule_id} ({h.reason})" for h in extra) + "."
    if d.flags:
        text += f" Flags: {', '.join(d.flags)}."
    if entry.approval in ("approved", "rejected"):
        text += f" Human {entry.approval} it."
    text += f" Outcome: {entry.outcome.value}."
    if entry.detail:
        text += f" ({entry.detail})"
    if a.rationale:
        text += f" Agent's reason: \"{a.rationale}\""
    return text

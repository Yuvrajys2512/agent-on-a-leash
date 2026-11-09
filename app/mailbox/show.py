"""Print the mock inbox: python -m app.mailbox.show"""
import json
from pathlib import Path

from app.contracts import Email, is_internal

INBOX = Path(__file__).resolve().parents[2] / "data" / "inbox.json"


def load(path: Path = INBOX) -> list[Email]:
    return [Email.model_validate(e) for e in json.loads(path.read_text(encoding="utf-8"))]


if __name__ == "__main__":
    emails = load()
    for e in emails:
        where = "internal" if is_internal(e.sender) else "external"
        print(f"{e.id}  {e.date:%H:%M}  {where:<8}  {e.sender:<42}  {e.subject}")
    print(f"\n{len(emails)} emails, all valid against Email")

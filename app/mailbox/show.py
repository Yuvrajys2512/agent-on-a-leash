"""Print the mock inbox: python -m app.mailbox.show"""
from app.contracts import is_internal
from app.mailbox import Mailbox

if __name__ == "__main__":
    emails = Mailbox().list()
    for e in emails:
        where = "internal" if is_internal(e.sender) else "external"
        print(f"{e.id}  {e.date:%H:%M}  {where:<8}  {e.sender:<42}  {e.subject}")
    print(f"\n{len(emails)} emails, all valid against Email")

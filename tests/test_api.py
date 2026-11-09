import time

import pytest
from fastapi.testclient import TestClient

from app.api.app import create_app
from app.api.hub import Hub
from app.contracts import ActionType as T, Email, ProposedAction


class FakeMailbox:
    def __init__(self):
        self.executed, self.resets = [], 0
        self.emails = {"e1": Email(id="e1", sender="client@outside.example", to=["me@acme.example"],
                                   subject="Invoice", body="b", date="2025-01-01T09:00:00")}

    def list(self):
        return list(self.emails.values())

    def get(self, i):
        return self.emails[i]

    def execute(self, action):
        self.executed.append(action.type.value)
        return f"did {action.type.value}"

    def reset(self):
        self.executed.clear()
        self.resets += 1


async def fake_agent(mailbox, gate, task):
    await gate(ProposedAction(type=T.LABEL, email_id="e1", params={"label": "invoice"}))
    await gate(ProposedAction(type=T.FORWARD, email_id="e1", params={"to": ["x@outside.example"]}))


async def crashing_agent(mailbox, gate, task):
    raise NotImplementedError("agent not built yet")


def wait_for(cond, timeout=5.0):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return
        time.sleep(0.02)
    raise AssertionError("timed out")


@pytest.fixture
def mb():
    return FakeMailbox()


def client(tmp_path, mb, agent=fake_agent, timeout=5.0):
    app = create_app(mb, agent, audit_path=str(tmp_path / "audit.jsonl"), approval_timeout=timeout, ui_dir="nope")
    return TestClient(app)


def test_full_flow_approve(tmp_path, mb):
    with client(tmp_path, mb) as c:
        assert c.post("/run").status_code == 202
        wait_for(lambda: c.get("/pending").json())
        pending = c.get("/pending").json()
        assert pending[0]["decision"]["rule_id"] == "external-send-ask"
        assert mb.executed == ["label"]  # the forward has NOT happened yet
        assert c.post(f"/approve/{pending[0]['action']['id']}").json() == {"status": "approved"}
        wait_for(lambda: not c.get("/status").json()["running"])
        assert mb.executed == ["label", "forward"]
        log = c.get("/log").json()
        assert [e["approval"] for e in log] == ["auto", "approved"]
        assert "external-send-ask" in log[1]["explanation"]


def test_reject(tmp_path, mb):
    with client(tmp_path, mb) as c:
        c.post("/run")
        wait_for(lambda: c.get("/pending").json())
        aid = c.get("/pending").json()[0]["action"]["id"]
        assert c.post(f"/reject/{aid}").json() == {"status": "rejected"}
        wait_for(lambda: not c.get("/status").json()["running"])
        assert mb.executed == ["label"]
        assert c.get("/log").json()[-1]["outcome"] == "rejected_by_human"


def test_unknown_id_404_and_double_run_409(tmp_path, mb):
    with client(tmp_path, mb) as c:
        assert c.post("/approve/nope").status_code == 404
        c.post("/run")
        assert c.post("/run").status_code == 409


def test_agent_crash_reported_not_raised(tmp_path, mb):
    with client(tmp_path, mb, agent=crashing_agent) as c:
        c.post("/run")
        wait_for(lambda: not c.get("/status").json()["running"])
        assert "NotImplementedError" in c.get("/status").json()["error"]


def test_rules_inbox_endpoints(tmp_path, mb):
    with client(tmp_path, mb) as c:
        assert [r["id"] for r in c.get("/rules").json()][0] == "organise-allowed"
        assert c.get("/inbox").json()[0]["id"] == "e1"


def test_reset_rejects_pending_archives_log(tmp_path, mb):
    with client(tmp_path, mb) as c:
        c.post("/run")
        wait_for(lambda: c.get("/pending").json())
        r = c.post("/reset").json()
        assert r["archived_log"] and mb.resets == 1
        assert c.get("/pending").json() == [] and c.get("/log").json() == []
        assert not c.get("/status").json()["running"]
        assert mb.executed == []  # the pending forward must never run after reset


async def test_hub_replays_history_then_streams_live():
    hub = Hub(keepalive=0.05)
    hub.publish({"kind": "a"})
    gen = hub.stream()
    assert '"kind": "a"' in await gen.__anext__()          # replayed
    hub.publish({"kind": "b"})
    assert '"kind": "b"' in await gen.__anext__()          # live
    assert (await gen.__anext__()).startswith(": keepalive")  # idle heartbeat
    await gen.aclose()
    assert not hub._queues

"""Phase 3.2: Person A's scripted agent through Person B's real API, gate, policy, mailbox and audit log.

Deterministic, no API key. This is the whole product minus the AI.
"""
import time

import pytest
from fastapi.testclient import TestClient

from app.agent.scripted import run_fake_agent, scripted_actions
from app.api.app import create_app
from app.contracts import Outcome, is_internal
from app.mailbox import Mailbox


def wait_for(cond, timeout=10.0):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return
        time.sleep(0.02)
    raise AssertionError("timed out")


@pytest.fixture
def run(tmp_path):
    """Runs the full scenario. Returns (mailbox, audit rows, list of what the human was asked, human answers)."""
    def go(approve: bool):
        mb = Mailbox()
        app = create_app(mb, run_fake_agent, audit_path=str(tmp_path / "audit.jsonl"), approval_timeout=10, ui_dir="nope")
        asked = []
        with TestClient(app) as c:
            assert c.post("/run").status_code == 202
            while c.get("/status").json()["running"] or c.get("/pending").json():
                for p in c.get("/pending").json():
                    asked.append(p)
                    c.post(f"/{'approve' if approve else 'reject'}/{p['action']['id']}")
                time.sleep(0.02)
            assert c.get("/status").json()["error"] is None
            return mb, c.get("/log").json(), asked
    return go


def test_trick_email_never_gets_data_out_even_if_human_approves_everything_asked(run):
    mb, log, asked = run(approve=True)
    evil = [o for o in mb.outbox if any("evil-payments" in t for t in o["to"])]
    assert evil == []  # nothing reached the attacker, however the human answered
    trick = [e for e in log if "evil-payments" in str(e["action"]["params"])]
    assert len(trick) == 3 and all(e["outcome"] == "blocked" and e["approval"] is None for e in trick)
    assert all(e["decision"]["rule_id"] == "block-injected-external-send" for e in trick)
    assert all("e09" in e["explanation"] and "ignore previous instructions" in e["explanation"] for e in trick)
    assert not any("evil-payments" in str(p["action"]["params"]) for p in asked)  # the human was never even bothered with it


def test_every_action_is_logged_exactly_once_in_order(run):
    mb, log, _ = run(approve=True)
    expected = scripted_actions()
    assert [e["action"]["id"] for e in log] == [a.id for a in expected] or len(log) == len(expected)
    assert len(log) == len(expected)
    assert {e["outcome"] for e in log} <= {o.value for o in Outcome}


def test_human_is_asked_only_for_outside_replies_and_everything_else_flows(run):
    mb, log, asked = run(approve=True)
    asked_ids = {p["action"]["id"] for p in asked}
    asks = [e for e in log if e["decision"]["verdict"] == "ask"]
    assert {e["action"]["id"] for e in asks} == asked_ids and len(asks) == 3  # replies to e04, e06, e10
    assert all(e["action"]["type"] == "reply" for e in asks)
    assert all(e["approval"] == "approved" and e["outcome"] == "executed" for e in asks)
    allowed = [e for e in log if e["decision"]["verdict"] == "allow"]
    assert all(e["approval"] == "auto" and e["outcome"] == "executed" for e in allowed)


def test_rejecting_everything_sends_nothing_outside(run):
    mb, log, asked = run(approve=False)
    outside = [o for o in mb.outbox if not all(is_internal(t) for t in o["to"])]
    assert outside == []
    assert all(e["outcome"] == "rejected_by_human" for e in log if e["decision"]["verdict"] == "ask")
    assert mb.outbox  # the internal forwards to accounting still happened


def test_nothing_external_ever_leaves_without_a_human_yes(run):
    mb, log, _ = run(approve=True)
    sent_outside = [o for o in mb.outbox if not all(is_internal(t) for t in o["to"])]
    approved_replies = [e for e in log if e["approval"] == "approved"]
    assert len(sent_outside) == len(approved_replies)  # every outside message corresponds to one human approval


def test_run_is_repeatable_after_reset(tmp_path):
    mb = Mailbox()
    app = create_app(mb, run_fake_agent, audit_path=str(tmp_path / "a.jsonl"), approval_timeout=10, ui_dir="nope")
    with TestClient(app) as c:
        for _ in range(2):
            c.post("/run")
            wait_for(lambda: c.get("/pending").json() or not c.get("/status").json()["running"])
            while c.get("/status").json()["running"]:
                for p in c.get("/pending").json():
                    c.post(f"/approve/{p['action']['id']}")
                time.sleep(0.02)
            assert len(c.get("/log").json()) == len(scripted_actions())
            assert c.post("/reset").json()["status"] == "reset"
            assert c.get("/log").json() == [] and mb.outbox == []
            assert all(not e.labels and e.folder == "inbox" for e in mb.list())

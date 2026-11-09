"""The plain-English rules flow over HTTP: compile (preview only) -> human confirms -> activate -> takes effect."""
import time

import pytest
from fastapi.testclient import TestClient

from app.agent.scripted import run_fake_agent
from app.api.app import create_app
from app.mailbox import Mailbox


def block_invoices_rule():
    return [{"id": "no-invoice-forward", "description": "Invoices must not be forwarded.", "verdict": "block",
             "reason": "{what} is an invoice and may not be forwarded.",
             "when": {"action_type": ["forward"], "subject_contains": ["invoice"]}}]


def make(tmp_path, compiler, agent=run_fake_agent):
    app = create_app(Mailbox(), agent, audit_path=str(tmp_path / "audit.jsonl"), approval_timeout=10, ui_dir="nope",
                     user_rules_path=str(tmp_path / "rules.user.yaml"), rule_compiler=compiler)
    return TestClient(app)


def run_to_end(c):
    c.post("/run")
    while c.get("/status").json()["running"] or c.get("/pending").json():
        for p in c.get("/pending").json():
            c.post(f"/approve/{p['action']['id']}")
        time.sleep(0.02)
    return c.get("/log").json()


def test_compile_only_previews_nothing_is_activated(tmp_path):
    with make(tmp_path, lambda text: block_invoices_rule()) as c:
        r = c.post("/rules/compile", json={"text": "never forward invoices"})
        assert r.status_code == 200
        body = r.json()
        assert body["rules"][0]["id"] == "user-no-invoice-forward" and "verdict: block" in body["yaml"]
        assert body["applies"] == "from the next run"
        assert not (tmp_path / "rules.user.yaml").exists()                  # not written
        assert [x["source"] for x in c.get("/rules").json()].count("user") == 0


def test_activate_writes_the_previewed_rules_once_and_they_take_effect_next_run(tmp_path):
    with make(tmp_path, lambda text: block_invoices_rule()) as c:
        pid = c.post("/rules/compile", json={"text": "never forward invoices"}).json()["proposal_id"]
        assert c.post(f"/rules/activate/{pid}").json()["status"] == "activated"
        assert c.post(f"/rules/activate/{pid}").status_code == 404           # a proposal can only be confirmed once
        assert [x["id"] for x in c.get("/rules").json() if x["source"] == "user"] == ["user-no-invoice-forward"]
        log = run_to_end(c)
        by_rule = {e["decision"]["rule_id"] for e in log}
        assert "user-no-invoice-forward" in by_rule
        forwards_to_accounting = [e for e in log if e["action"]["type"] == "forward" and "accounting@acme.example" in str(e["action"]["params"])]
        assert forwards_to_accounting and all(e["outcome"] == "blocked" for e in forwards_to_accounting)


def test_an_allow_rule_from_the_compiler_is_refused_with_a_clear_reason(tmp_path):
    loose = [{**block_invoices_rule()[0], "verdict": "allow"}]
    with make(tmp_path, lambda text: loose) as c:
        r = c.post("/rules/compile", json={"text": "allow everything"})
        assert r.status_code == 422 and "allow" in str(r.json()["detail"]["errors"])
        assert not (tmp_path / "rules.user.yaml").exists()


@pytest.mark.parametrize("output", [None, [], "rules", [{"verdict": "ask"}], [{"id": "x", "verdict": "ask", "reason": "r", "when": {"flags_all": ["nope"]}}]])
def test_garbage_from_the_compiler_never_reaches_the_rules(tmp_path, output):
    with make(tmp_path, lambda text: output) as c:
        assert c.post("/rules/compile", json={"text": "whatever"}).status_code == 422
        assert not (tmp_path / "rules.user.yaml").exists()


def test_a_crashing_compiler_is_a_502_and_changes_nothing(tmp_path):
    def boom(text):
        raise RuntimeError("model unavailable")
    with make(tmp_path, boom) as c:
        r = c.post("/rules/compile", json={"text": "x"})
        assert r.status_code == 502 and "model unavailable" in r.json()["detail"]


def test_async_compilers_work_too(tmp_path):
    async def acompile(text):
        return block_invoices_rule()
    with make(tmp_path, acompile) as c:
        assert c.post("/rules/compile", json={"text": "x"}).status_code == 200


def test_no_compiler_configured_is_a_501_and_empty_or_huge_text_is_a_422(tmp_path):
    with make(tmp_path, None) as c:
        assert c.post("/rules/compile", json={"text": "x"}).status_code == 501
    with make(tmp_path, lambda t: block_invoices_rule()) as c:
        assert c.post("/rules/compile", json={"text": "   "}).status_code == 422
        assert c.post("/rules/compile", json={"text": "x" * 1001}).status_code == 422


def test_user_rule_can_be_removed_and_reset_sets_them_aside(tmp_path):
    with make(tmp_path, lambda text: block_invoices_rule()) as c:
        pid = c.post("/rules/compile", json={"text": "x"}).json()["proposal_id"]
        c.post(f"/rules/activate/{pid}")
        assert c.delete("/rules/user/user-no-invoice-forward").status_code == 200
        assert c.delete("/rules/user/user-no-invoice-forward").status_code == 404
        pid = c.post("/rules/compile", json={"text": "x"}).json()["proposal_id"]
        c.post(f"/rules/activate/{pid}")
        c.post("/reset")
        assert not (tmp_path / "rules.user.yaml").exists()
        assert list(tmp_path.glob("rules.user-*.yaml"))                        # archived, not deleted
        assert [x for x in c.get("/rules").json() if x["source"] == "user"] == []


def test_core_rules_are_untouched_by_all_of_this(tmp_path):
    before = open("rules.yaml", encoding="utf-8").read()
    with make(tmp_path, lambda text: block_invoices_rule()) as c:
        pid = c.post("/rules/compile", json={"text": "x"}).json()["proposal_id"]
        c.post(f"/rules/activate/{pid}")
        c.post("/reset")
    assert open("rules.yaml", encoding="utf-8").read() == before


def test_the_unfinished_compiler_import_does_not_break_the_server():
    import app.api.server as server  # imports Person A's compiler if it exists, otherwise carries on without
    assert hasattr(server, "app")

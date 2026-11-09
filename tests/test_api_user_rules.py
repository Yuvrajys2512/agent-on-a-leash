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


# ---- Person A's real compile_rule, behind the adapter, with a fake model ----
import json
from types import SimpleNamespace

from app.api.compiler_adapter import compile_via_agent
from app.policy.rules import Rule
from app.policy.user_rules import RuleRefused, describe_rule


class FakeClaude:
    def __init__(self, payload, stop="end_turn"):
        self.payload, self.stop = payload, stop
        self.messages = SimpleNamespace(create=self._create)

    async def _create(self, **kwargs):
        return SimpleNamespace(stop_reason=self.stop, content=[SimpleNamespace(type="text", text=json.dumps(self.payload))])


def model_says(verdict="block", **rule):
    return {"expressible": True, "problem": "", "rule": {"id": "outside-block", "description": "d", "action_type": ["forward"],
            "recipient": "external", "flags_all": [], "verdict": verdict, "reason": "Outside is blocked.", **rule}}


async def test_adapter_turns_a_good_model_answer_into_a_rule_dict():
    rules = await compile_via_agent("never forward outside", client=FakeClaude(model_says()))
    assert rules[0]["id"] == "user-outside-block" and rules[0]["verdict"] == "block"


async def test_adapter_raises_refused_with_the_compilers_plain_reason_for_allow():
    with pytest.raises(RuleRefused, match="tighter"):
        await compile_via_agent("always allow", client=FakeClaude(model_says(verdict="allow")))


async def test_adapter_raises_refused_when_the_engine_cannot_express_it():
    payload = {"expressible": False, "problem": "The engine cannot check words in an email.", "rule": model_says()["rule"]}
    with pytest.raises(RuleRefused, match="words"):
        await compile_via_agent("anything mentioning salary", client=FakeClaude(payload))


def test_route_returns_the_compilers_reason_as_a_422_and_a_readback_for_good_rules(tmp_path):
    async def refuses(text):
        raise RuleRefused("Your rules can only make the leash tighter.")
    with make(tmp_path, refuses) as c:
        r = c.post("/rules/compile", json={"text": "allow everything"})
        assert r.status_code == 422 and "tighter" in r.json()["detail"]["errors"][0]
    with make(tmp_path, lambda t: block_invoices_rule()) as c:
        body = c.post("/rules/compile", json={"text": "x"}).json()
        assert body["previews"] == ["When the agent tries to forward with \"invoice\" in the subject: block it."]


def test_describe_rule_reads_naturally():
    r = Rule.model_validate({"id": "user-x", "verdict": "ask", "reason": "r", "when": {
        "action_type": ["reply", "forward"], "recipient": "external", "recipient_domain": ["a.example"], "flags_all": ["pii"]}})
    assert describe_rule(r) == "When the agent tries to reply or forward to someone outside the company to a.example and pii is detected: ask you first."

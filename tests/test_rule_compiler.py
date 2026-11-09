"""Phase 5.A1-A3: plain English -> rule. Fake Claude returns canned JSON; the code-side checks are what's tested."""
import json
from types import SimpleNamespace

import pytest

from app.agent.rule_compiler import compile_rule, merge_rules
from app.contracts import ActionType as T, ProposedAction, Verdict as V
from app.mailbox import Mailbox
from app.policy.engine import PolicyEngine


class FakeClient:
    def __init__(self, reply, stop="end_turn"):
        self.reply, self.stop, self.calls = reply, stop, []
        self.messages = self

    async def create(self, **kw):
        self.calls.append(kw)
        text = self.reply if isinstance(self.reply, str) else json.dumps(self.reply)
        return SimpleNamespace(stop_reason=self.stop, content=[SimpleNamespace(type="text", text=text)])


def ans(verdict, acts=(), recipient="any", flags=(), rid="r", ok=True, problem=""):
    return {"expressible": ok, "problem": problem, "rule": {
        "id": rid, "description": "d", "action_type": list(acts), "recipient": recipient,
        "flags_all": list(flags), "verdict": verdict, "reason": "Your rule fired."}}


GOOD = [  # 5.A1: five or more sentences -> valid rules
    ("Never send anything outside without asking me", ans("ask", ["reply", "forward"], "external", rid="ask-external"), V.ASK),
    ("Ask me before moving any email", ans("ask", ["move"], rid="ask-move"), V.ASK),
    ("Block forwards with personal data to outside", ans("block", ["forward"], "external", ["pii"]), V.BLOCK),
    ("Block replies to outside if it looks like an attack", ans("block", ["reply"], "external", ["injection_suspected"]), V.BLOCK),
    ("Ask me before forwarding an IBAN anywhere", ans("ask", ["forward"], flags=["pii:iban"]), V.ASK),
    ("Ask me before labelling anything", ans("ask", ["label"]), V.ASK),
]


@pytest.mark.parametrize("sentence,reply,verdict", GOOD)
async def test_sentences_become_valid_rules_with_a_preview(sentence, reply, verdict):
    p = await compile_rule(sentence, client=FakeClient(reply))
    assert p.ok and p.rule.verdict == verdict and p.rule.id.startswith("user-")
    assert p.preview and p.problem == ""


async def test_preview_reads_back_in_plain_words():
    p = await compile_rule("x", client=FakeClient(ans("block", ["forward"], "external", ["pii"])))
    assert p.preview == "For forward to someone outside the company, when what would be sent contains personal data (IBAN, phone number or a person's name): block it."


async def test_loosening_rule_is_refused():  # 5.A2: can only tighten
    p = await compile_rule("Always allow forwarding outside", client=FakeClient(ans("allow", ["forward"], "external")))
    assert not p.ok and "tighter" in p.problem


async def test_unsupported_sentence_gives_clear_error():
    p = await compile_rule("Anything mentioning salary needs approval",
                           client=FakeClient(ans("ask", ok=False, problem="It can't check words in the email.")))
    assert not p.ok and p.rule is None and "words" in p.problem


@pytest.mark.parametrize("reply", ["not json", json.dumps({"rule": {"verdict": "maybe"}}),  # 5.A3: invalid -> error
                                   json.dumps(ans("ask", ["delete"])), json.dumps(ans("ask", flags=["vip"])), "[]"])
async def test_invalid_output_is_an_error(reply):
    p = await compile_rule("x", client=FakeClient(reply))
    assert not p.ok and p.rule is None and p.problem


async def test_cut_off_or_empty_is_an_error():
    assert not (await compile_rule("x", client=FakeClient(ans("ask"), stop="max_tokens"))).ok
    assert not (await compile_rule("   ", client=FakeClient(ans("ask")))).ok


async def test_compile_never_touches_the_engine():  # 5.A2: never auto-activated
    engine = PolicyEngine("rules.yaml")
    before = [r.id for r in engine.rules]
    await compile_rule("Ask me before moving any email", client=FakeClient(ans("ask", ["move"])))
    assert [r.id for r in engine.rules] == before


async def _rule(reply):
    return (await compile_rule("x", client=FakeClient(reply))).rule


async def test_user_rule_cannot_soften_the_core_trick_block():
    engine = PolicyEngine("rules.yaml")
    ask_all = await _rule(ans("ask", ["forward"], "external", rid="ask-forwards"))
    engine.rules = merge_rules(engine.rules, [ask_all])
    d = engine.evaluate(ProposedAction(type=T.FORWARD, email_id="e01", params={"to": ["collect@evil-payments.example"]},
                                       context_email_ids=["e01", "e09"]), Mailbox())
    assert d.verdict == V.BLOCK and d.rule_id == "block-injected-external-send"
    assert "user-ask-forwards" in [h.rule_id for h in d.hits]   # it still shows up in the log


async def test_user_rule_beats_the_core_allow():
    engine = PolicyEngine("rules.yaml")
    engine.rules = merge_rules(engine.rules, [await _rule(ans("ask", ["move"], rid="ask-move"))])
    d = engine.evaluate(ProposedAction(type=T.MOVE, email_id="e05", params={"folder": "Archive"}), Mailbox())
    assert d.verdict == V.ASK and d.rule_id == "user-ask-move"


def test_merge_drops_allow_and_duplicate_ids():
    core = PolicyEngine("rules.yaml").rules
    sneaky = core[0].model_copy(update={"verdict": V.ALLOW, "id": "user-x"})
    dup = core[1].model_copy(update={"verdict": V.ASK})
    merged = merge_rules(core, [sneaky, dup])
    assert [r.id for r in merged if r.id.startswith("user-")] == [] and len(merged) == len(core)

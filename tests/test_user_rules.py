"""Phase 5.A engine side: checking rules that came from plain English, storing them, and the tighten-only overlay."""
import pytest

from app.contracts import ActionType as T, Email, ProposedAction, Verdict as V
from app.mailbox import Mailbox
from app.policy.engine import PolicyEngine
from app.policy.rules import RulesError, load_rules, rules_format_help
from app.policy.user_rules import MAX_RULES, UserRulesStore, check_proposed, rules_to_yaml

CORE_IDS = {r.id for r in load_rules("rules.yaml").rules}


def good(**over):
    base = {"id": "ask-outside", "description": "Ask before anything leaves.", "verdict": "ask",
            "reason": "{what} would go to {recipients}.", "when": {"action_type": ["reply", "forward"], "recipient": "external"}}
    return {**base, **over}


# ------------------------------------------------------------------ validation
def test_a_good_rule_passes_and_gets_a_user_prefix():
    c = check_proposed([good()], CORE_IDS)
    assert c.ok and c.rules[0].id == "user-ask-outside" and any("id set to" in w for w in c.warnings)


def test_allow_rules_are_refused_because_they_could_loosen_the_default():
    c = check_proposed([good(verdict="allow")], CORE_IDS)
    assert not c.ok and "allow" in c.errors[0] and "loosen" in c.errors[0]


def test_one_bad_rule_blocks_the_whole_batch_from_being_ok():
    c = check_proposed([good(), good(id="x", verdict="allow")], CORE_IDS)
    assert c.errors and not c.ok


@pytest.mark.parametrize("bad", [None, [], "rules", {"id": "x"}, [1, 2], [None]])
def test_garbage_is_refused_with_a_readable_message(bad):
    c = check_proposed(bad)
    assert not c.ok and c.errors


def test_unknown_flag_is_refused_because_it_would_never_fire():
    c = check_proposed([good(when={"flags_all": ["definitely_not_a_flag"]})])
    assert not c.ok and "never fire" in c.errors[0]


def test_flag_families_and_variants_are_accepted():
    c = check_proposed([good(when={"flags_all": ["pii:iban", "injection_suspected:e09", "tainted"]})])
    assert c.ok


@pytest.mark.parametrize("extra", [{"typo_field": 1}, {"when": {"recipient": "everyone"}}, {"verdict": "maybe"}])
def test_invalid_fields_are_refused(extra):
    assert not check_proposed([good(**extra)]).ok


def test_ids_that_clash_with_core_or_each_other_are_renamed_not_overwritten():
    c = check_proposed([good(id="external-send-ask"), good(id="external-send-ask")], CORE_IDS)
    assert c.ok and len({r.id for r in c.rules}) == 2 and not ({r.id for r in c.rules} & CORE_IDS)


def test_limits():
    assert not check_proposed([good(id=f"r{i}") for i in range(MAX_RULES + 1)]).ok
    assert not check_proposed([good(reason="x" * 301)]).ok
    assert not check_proposed([good(reason="  ")]).ok


def test_domains_are_cleaned_and_odd_input_warns():
    c = check_proposed([good(when={"recipient_domain": ["@Evil-Payments.Example", "*.other.example", "localhost"]})])
    assert c.rules[0].when.recipient_domain == ["evil-payments.example", "other.example", "localhost"]
    assert any("without a dot" in w for w in c.warnings)


def test_a_rule_without_conditions_is_allowed_but_warned_about():
    c = check_proposed([good(when={})])
    assert c.ok and any("EVERY action" in w for w in c.warnings)


def test_unknown_placeholder_warns():
    assert any("{nope}" in w for w in check_proposed([good(reason="hi {nope}")]).warnings)


def test_format_help_mentions_every_condition_and_the_allow_ban():
    h = rules_format_help()
    for word in ("recipient_domain", "sender_domain", "subject_contains", "flags_all", "may NOT use allow", "pii:iban"):
        assert word in h


# ------------------------------------------------------------------ store
def test_store_roundtrip_history_and_remove(tmp_path):
    s = UserRulesStore(str(tmp_path / "rules.user.yaml"))
    assert s.load() == []
    added = s.add(check_proposed([good()], CORE_IDS).rules, english="ask me before sending outside")
    assert [r.id for r in s.load()] == [r.id for r in added]
    assert "STRICTER" in (tmp_path / "rules.user.yaml").read_text(encoding="utf-8")
    assert "ask me before sending outside" in (tmp_path / "rules.user.history.jsonl").read_text(encoding="utf-8")
    assert s.remove(added[0].id) and not s.path.exists() and not s.remove("nope")


def test_store_refuses_allow_even_if_someone_skips_the_check(tmp_path):
    from app.policy.rules import Rule
    s = UserRulesStore(str(tmp_path / "u.yaml"))
    with pytest.raises(RulesError, match="allow"):
        s.add([Rule.model_validate(good(id="user-x", verdict="allow"))])
    assert not s.path.exists()


def test_adding_twice_keeps_both_and_never_duplicates_ids(tmp_path):
    s = UserRulesStore(str(tmp_path / "u.yaml"))
    s.add(check_proposed([good()], CORE_IDS).rules)
    s.add(check_proposed([good()], CORE_IDS | s.ids()).rules)
    ids = [r.id for r in s.load()]
    assert len(ids) == 2 and len(set(ids)) == 2


def test_archive_sets_the_file_aside(tmp_path):
    s = UserRulesStore(str(tmp_path / "u.yaml"))
    s.add(check_proposed([good()], CORE_IDS).rules)
    moved = s.archive()
    assert moved and moved.exists() and not s.path.exists() and s.archive() is None


def test_yaml_written_is_loadable(tmp_path):
    p = tmp_path / "u.yaml"
    p.write_text(rules_to_yaml(check_proposed([good(), good(id="b", when={"subject_contains": ["invoice"]})], CORE_IDS).rules))
    assert len(load_rules(p).rules) == 2


# ------------------------------------------------------------------ the engine overlay (tighten only)
@pytest.fixture
def mb():
    return Mailbox()


def engine_with(tmp_path, *rules):
    store = UserRulesStore(str(tmp_path / "u.yaml"))
    store.add(check_proposed(list(rules), CORE_IDS).rules)
    return PolicyEngine("rules.yaml", str(store.path))


def fwd(email_id, to, ctx=None):
    return ProposedAction(type=T.FORWARD, email_id=email_id, params={"to": to}, context_email_ids=ctx or [email_id])


def test_user_block_beats_a_core_ask(tmp_path, mb):
    eng = engine_with(tmp_path, good(id="no-partner", verdict="block", reason="{recipients} is blocked.",
                                     when={"action_type": ["forward"], "recipient_domain": ["turkubio.example"]}))
    plain = PolicyEngine("rules.yaml").evaluate(fwd("e02", ["jussi.laine@turkubio.example"]), mb)
    d = eng.evaluate(fwd("e02", ["jussi.laine@turkubio.example"]), mb)
    assert plain.verdict == V.ASK and d.verdict == V.BLOCK and d.rule_id == "user-no-partner"
    assert d.reason == "jussi.laine@turkubio.example is blocked."
    assert d.hits[0].rule_id == "user-no-partner" and "external-send-ask" in [h.rule_id for h in d.hits]


def test_user_ask_beats_a_core_allow(tmp_path, mb):
    eng = engine_with(tmp_path, good(id="ask-invoices", reason="Invoices need a look.",
                                     when={"action_type": ["forward"], "subject_contains": ["INVOICE"]}))
    d = eng.evaluate(fwd("e01", ["accounting@acme.example"]), mb)  # core says allow (internal)
    assert d.verdict == V.ASK and d.rule_id == "user-ask-invoices"
    assert eng.evaluate(fwd("e04", ["accounting@acme.example"]), mb).verdict == V.ALLOW  # not an invoice


def test_a_user_ask_can_never_loosen_a_core_block(tmp_path, mb):
    eng = engine_with(tmp_path, good(id="soft", when={"action_type": ["forward"]}))
    d = eng.evaluate(fwd("e01", ["collect@evil-payments.example"], ["e01", "e09"]), mb)
    assert d.verdict == V.BLOCK and d.rule_id == "block-injected-external-send"


def test_the_strictest_user_rule_wins_and_all_matches_are_logged(tmp_path, mb):
    eng = engine_with(tmp_path,
                      good(id="a", when={"action_type": ["forward"]}),
                      good(id="b", verdict="block", reason="no.", when={"action_type": ["forward"]}))
    d = eng.evaluate(fwd("e04", ["accounting@acme.example"]), mb)
    assert d.verdict == V.BLOCK and d.rule_id == "user-b"
    assert {"user-a", "user-b", "internal-send-allowed"} <= {h.rule_id for h in d.hits}


def test_sender_domain_and_subdomain_matching(tmp_path, mb):
    eng = engine_with(tmp_path, good(id="s", verdict="block", reason="no.",
                                     when={"action_type": ["label"], "sender_domain": ["nordprint.example"]}))
    lab = lambda i: ProposedAction(type=T.LABEL, email_id=i, params={"label": "x"})  # noqa: E731
    assert eng.evaluate(lab("e01"), mb).verdict == V.BLOCK       # from billing@nordprint.example
    assert eng.evaluate(lab("e02"), mb).verdict == V.ALLOW       # from cloudhost
    (tmp_path / "sub").mkdir()
    eng2 = engine_with(tmp_path / "sub", good(id="d", verdict="block", reason="no.",
                                              when={"recipient_domain": ["turkubio.example"]}))
    assert eng2.evaluate(fwd("e02", ["x@mail.turkubio.example"]), mb).verdict == V.BLOCK  # subdomain counts
    assert eng2.evaluate(fwd("e02", ["x@notturkubio.example"]), mb).verdict != V.BLOCK     # but not a lookalike suffix


def test_recipient_domain_never_matches_actions_that_send_nothing(tmp_path, mb):
    eng = engine_with(tmp_path, good(id="r", verdict="block", reason="no.", when={"recipient_domain": ["nordprint.example"]}))
    assert eng.evaluate(ProposedAction(type=T.LABEL, email_id="e01", params={"label": "x"}), mb).verdict == V.ALLOW


def test_a_hand_edited_file_with_an_allow_rule_is_rejected_loudly(tmp_path):
    p = tmp_path / "u.yaml"
    p.write_text("rules:\n  - {id: user-x, verdict: allow, reason: 'x'}\n")
    with pytest.raises(RulesError, match="may only ask or block"):
        PolicyEngine("rules.yaml", str(p))


def test_a_user_rule_id_clashing_with_a_core_id_is_rejected_loudly(tmp_path):
    p = tmp_path / "u.yaml"
    p.write_text("rules:\n  - {id: external-send-ask, verdict: ask, reason: 'x'}\n")
    with pytest.raises(RulesError, match="clash"):
        PolicyEngine("rules.yaml", str(p))


def test_no_user_file_means_exactly_the_old_behaviour(mb):
    a, b = PolicyEngine("rules.yaml"), PolicyEngine("rules.yaml", "does-not-exist.yaml")
    act = fwd("e02", ["x@outside.example"])
    assert a.evaluate(act, mb) == b.evaluate(act, mb)

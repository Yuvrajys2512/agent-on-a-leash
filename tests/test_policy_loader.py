import pytest

from app.policy.rules import RulesError, load_rules


def write(tmp_path, text):
    p = tmp_path / "r.yaml"
    p.write_text(text, encoding="utf-8")
    return p


def test_real_rules_file_loads():
    rs = load_rules("rules.yaml")
    ids = [r.id for r in rs.rules]
    assert ids[0] == "organise-allowed" and ids[-1] == "external-send-ask"
    # the block rule must come before the generic external-send rule, because the first match wins
    assert ids.index("block-injected-external-send") < ids.index("external-send-ask")


def test_missing_file(tmp_path):
    with pytest.raises(RulesError, match="not found"):
        load_rules(tmp_path / "nope.yaml")


def test_bad_yaml(tmp_path):
    with pytest.raises(RulesError, match="not valid YAML"):
        load_rules(write(tmp_path, "rules: [unclosed"))


def test_not_a_mapping(tmp_path):
    with pytest.raises(RulesError, match="top-level"):
        load_rules(write(tmp_path, "- just a list"))


def test_unknown_verdict(tmp_path):
    with pytest.raises(RulesError, match="verdict"):
        load_rules(write(tmp_path, "rules:\n  - {id: a, verdict: maybe, reason: x}"))


def test_unknown_field_is_rejected(tmp_path):
    with pytest.raises(RulesError, match="Invalid rules"):
        load_rules(write(tmp_path, "rules:\n  - {id: a, verdict: allow, reason: x, wen: {}}"))


def test_duplicate_ids(tmp_path):
    txt = "rules:\n  - {id: a, verdict: allow, reason: x}\n  - {id: a, verdict: ask, reason: y}"
    with pytest.raises(RulesError, match="Duplicate"):
        load_rules(write(tmp_path, txt))

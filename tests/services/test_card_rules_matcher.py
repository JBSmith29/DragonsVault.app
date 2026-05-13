"""Tests for keyword ability detection."""

from __future__ import annotations

from core.shared.utils.card_rules_matcher import (
    find_keyword_abilities,
    attach_rule_snippets,
    KEYWORD_RULE_INDEX,
)


def test_find_keyword_abilities_detects_common_keywords():
    text = "Flying, Vigilance, Lifelink"
    matches = find_keyword_abilities(text)
    keywords = {m.keyword for m in matches}
    assert {"Flying", "Vigilance", "Lifelink"}.issubset(keywords)


def test_find_keyword_abilities_skips_reminder_text_duplicates():
    text = (
        "Prowess (Whenever you cast a noncreature spell, this creature gets +1/+1 "
        "until end of turn.)"
    )
    matches = find_keyword_abilities(text)
    # Prowess should appear exactly once even though its reminder text mentions
    # the ability name in the wrapper.
    assert [m.keyword for m in matches] == ["Prowess"]


def test_find_keyword_abilities_case_insensitive_and_word_boundaries():
    text = "hasTe, SCRY 2, Tramplling, Menace"
    matches = find_keyword_abilities(text)
    keywords = {m.keyword for m in matches}
    # "Tramplling" shouldn't match "Trample"; Haste, Scry, Menace should.
    assert keywords == {"Haste", "Scry", "Menace"}


def test_attach_rule_snippets_fills_rule_text(monkeypatch):
    import core.shared.utils.card_rules_matcher as matcher

    matches = find_keyword_abilities("Flying")
    monkeypatch.setattr(matcher, "lookup_magic_rule", lambda rule: f"Rule {rule} text")
    hydrated = attach_rule_snippets(matches)
    assert hydrated[0].rule_text == f"Rule {KEYWORD_RULE_INDEX['Flying']} text"


def test_find_keyword_abilities_returns_empty_for_blank():
    assert find_keyword_abilities("") == []
    assert find_keyword_abilities(None) == []

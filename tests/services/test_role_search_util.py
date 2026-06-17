from core.domains.cards.services.role_search_util import (
    role_query_like_patterns,
    role_query_tokens,
    split_role_query_terms,
    text_matches_role_tokens,
)


def test_split_terms_on_commas_and_semicolons():
    assert split_role_query_terms("flying, trample") == ["flying", "trample"]
    assert split_role_query_terms(" flying ;  goblin , ") == ["flying", "goblin"]
    assert split_role_query_terms("flying") == ["flying"]
    assert split_role_query_terms("") == []
    assert split_role_query_terms("  ,  ; ") == []


def test_patterns_match_word_starts_only():
    assert role_query_like_patterns("ramp") == ["ramp%", "% ramp%"]
    # blank / whitespace yields no patterns (no filter applied)
    assert role_query_like_patterns("   ") == []


def test_hyphen_and_underscore_fold_to_spaces():
    tokens = role_query_tokens("go-tall")
    assert "go-tall" in tokens and "go tall" in tokens
    pats = role_query_like_patterns("go_tall")
    assert "go tall%" in pats


def test_text_matches_at_word_boundaries():
    forest = role_query_tokens("forest")
    assert text_matches_role_tokens("Basic Land — Forest", forest)          # land type
    assert text_matches_role_tokens("Legendary Creature — Goblin", role_query_tokens("goblin"))
    assert text_matches_role_tokens("Card Advantage Utility", role_query_tokens("advantage"))


def test_does_not_match_mid_word_substring():
    # the classic false positive: "ramp" must not match "Trample"
    assert not text_matches_role_tokens("Trample", role_query_tokens("ramp"))
    assert not text_matches_role_tokens("Vigilance", role_query_tokens("lance"))


def test_empty_inputs_never_match():
    assert not text_matches_role_tokens("Forest", set())
    assert not text_matches_role_tokens("", role_query_tokens("forest"))

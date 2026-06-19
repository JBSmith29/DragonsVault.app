from core.domains.cards.services import card_autocomplete_service as ac


_SAMPLE_PRINTS = [
    {"name": "Lightning Bolt"},
    {"name": "Lightning Bolt"},  # duplicate printing — collapses to one
    {"name": "Lightning Helix"},
    {"name": "Bolt Bend"},
    {"name": "Sol Ring // Sol Ring"},  # reversible artifact -> "Sol Ring"
    {"name": "Delver of Secrets // Insectile Aberration"},  # real DFC kept whole
    {"name": "Forest"},
    {"name": ""},  # ignored
]


def _patch_cache(monkeypatch):
    monkeypatch.setattr(ac.sc, "ensure_cache_loaded", lambda *a, **k: True)
    monkeypatch.setattr(ac.sc, "get_all_prints", lambda: _SAMPLE_PRINTS)
    monkeypatch.setattr(ac.sc, "cache_epoch", lambda: 12345)
    ac._name_index.cache_clear()


def test_prefix_matches_rank_first(monkeypatch):
    _patch_cache(monkeypatch)
    out = ac.autocomplete_card_names("lightning", limit=10)
    assert out[:2] == ["Lightning Bolt", "Lightning Helix"]
    # one entry per distinct name (printing dupes collapsed)
    assert out.count("Lightning Bolt") == 1


def test_substring_fallback_fills_remaining(monkeypatch):
    _patch_cache(monkeypatch)
    out = ac.autocomplete_card_names("bolt", limit=10)
    # "Bolt Bend" (prefix) ranks before "Lightning Bolt" (substring)
    assert out[0] == "Bolt Bend"
    assert "Lightning Bolt" in out


def test_reversible_name_collapses_but_dfc_kept(monkeypatch):
    _patch_cache(monkeypatch)
    assert ac.autocomplete_card_names("sol", limit=10) == ["Sol Ring"]
    assert ac.autocomplete_card_names("delver", limit=10) == [
        "Delver of Secrets // Insectile Aberration"
    ]


def test_short_query_returns_empty(monkeypatch):
    _patch_cache(monkeypatch)
    assert ac.autocomplete_card_names("a", limit=10) == []
    assert ac.autocomplete_card_names("", limit=10) == []


def test_limit_is_clamped(monkeypatch):
    _patch_cache(monkeypatch)
    assert len(ac.autocomplete_card_names("l", limit=10)) == 0  # too short
    assert len(ac.autocomplete_card_names("li", limit=1)) == 1

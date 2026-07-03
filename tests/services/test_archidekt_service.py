import json

import pytest

from core.domains.games.services import archidekt_service as A


class _FakeResponse:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status

    def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


def _mock_get(monkeypatch, by_url):
    def fake_get(url, timeout=None, headers=None):
        for fragment, resp in by_url.items():
            if fragment in url:
                return resp
        return _FakeResponse({}, status=404)

    monkeypatch.setattr(A, "safe_get", fake_get)


def test_normalize_username_handles_url_and_at():
    assert A.normalize_username("https://archidekt.com/u/Alice/") == "Alice"
    assert A.normalize_username("archidekt.com/user/Bob?tab=decks") == "Bob"
    assert A.normalize_username("  @Carol ") == "Carol"
    assert A.normalize_username("Dave") == "Dave"


def test_list_commander_decks_filters_to_edh(monkeypatch):
    payload = {
        "count": 3,
        "next": None,
        "results": [
            {"id": 1, "name": "Azula", "deckFormat": 3, "edhBracket": 2, "size": 100},
            {"id": 2, "name": "Standard Deck", "deckFormat": 1, "edhBracket": None, "size": 60},
            {"id": 3, "name": "Untitled", "deckFormat": 3, "edhBracket": None, "size": 100},
        ],
    }
    _mock_get(monkeypatch, {"/decks/v3/": _FakeResponse(payload)})

    decks = A.list_commander_decks("alice")
    assert [d["id"] for d in decks] == [1, 3]  # only Commander (deckFormat 3)
    assert decks[0]["bracket"] == 2
    assert decks[0]["url"].endswith("/decks/1")


def test_list_commander_decks_rejects_bad_username(monkeypatch):
    _mock_get(monkeypatch, {})
    with pytest.raises(A.ArchidektError):
        A.list_commander_decks("bad name!!")
    with pytest.raises(A.ArchidektError):
        A.list_commander_decks("")


def test_fetch_deck_parses_commander_bracket_and_cards(monkeypatch):
    deck_payload = {
        "id": 42,
        "name": "Azula attempt",
        "deckFormat": 3,
        "edhBracket": 1,
        "categories": [
            {"name": "Commander", "includedInDeck": True},
            {"name": "Maybeboard", "includedInDeck": False},
        ],
        "cards": [
            {"quantity": 1, "categories": ["Commander"], "card": {"oracleCard": {"name": "Fire Lord Azula"}}},
            {"quantity": 1, "categories": ["Ramp"], "card": {"oracleCard": {"name": "Sol Ring"}}},
            {"quantity": 1, "categories": ["Maybeboard"], "card": {"oracleCard": {"name": "Not In Deck"}}},
            {"quantity": 1, "categories": [], "card": {"oracleCard": {"name": "Command Tower"}}},
        ],
    }
    _mock_get(monkeypatch, {"/decks/42/": _FakeResponse(deck_payload)})

    deck = A.fetch_deck("42")
    assert deck["name"] == "Azula attempt"
    assert deck["commanders"] == ["Fire Lord Azula"]
    assert deck["commander_name"] == "Fire Lord Azula"
    assert deck["bracket"] == 1
    names = {c["name"] for c in deck["cards"]}
    assert names == {"Sol Ring", "Command Tower"}  # commander + maybeboard excluded
    assert deck["card_count"] == 3  # 2 mainboard + 1 commander


def test_fetch_deck_rejects_non_numeric_id(monkeypatch):
    _mock_get(monkeypatch, {})
    with pytest.raises(A.ArchidektError):
        A.fetch_deck("abc")


def test_missing_resource_raises(monkeypatch):
    _mock_get(monkeypatch, {"/decks/v3/": _FakeResponse({}, status=404)})
    with pytest.raises(A.ArchidektError):
        A.list_commander_decks("ghost")

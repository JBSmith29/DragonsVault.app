"""Unit tests for the deck importers (HTTP mocked)."""

import pytest

from core.domains.game_vault.services.importers import (
    archidekt,
    moxfield,
    mtggoldfish,
)
from core.domains.game_vault.services.importers.base import (
    DeckImportError,
    detect_source,
)


class _Resp:
    def __init__(self, payload=None, text="", status=200):
        self._payload = payload
        self.text = text
        self.status_code = status

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


def test_detect_source():
    assert detect_source("https://archidekt.com/decks/123") == "archidekt"
    assert detect_source("https://www.moxfield.com/decks/aBc_1-2") == "moxfield"
    assert detect_source("http://mtggoldfish.com/deck/456") == "mtggoldfish"
    assert detect_source("https://example.com/deck/1") is None
    assert detect_source("") is None


def test_archidekt_fetch(monkeypatch):
    payload = {
        "id": 42,
        "name": "Atraxa",
        "deckFormat": 3,
        "edhBracket": 4,
        "categories": [{"name": "Maybeboard", "includedInDeck": False}],
        "cards": [
            {"quantity": 1, "categories": ["Commander"],
             "card": {"oracleCard": {"name": "Atraxa, Praetors' Voice"}}},
            {"quantity": 1, "categories": [],
             "card": {"oracleCard": {"name": "Sol Ring"}}},
            {"quantity": 1, "categories": ["Maybeboard"],
             "card": {"oracleCard": {"name": "Rhystic Study"}}},
        ],
    }
    monkeypatch.setattr(archidekt, "safe_get", lambda url, **kw: _Resp(payload=payload))
    deck = archidekt.fetch_deck("https://archidekt.com/decks/42/atraxa")
    assert deck.name == "Atraxa"
    assert deck.commanders == ["Atraxa, Praetors' Voice"]
    assert deck.bracket == 4
    assert {c["name"] for c in deck.cards} == {"Sol Ring"}  # maybeboard excluded
    assert deck.card_count == 2  # sol ring + commander


def test_archidekt_bad_id():
    with pytest.raises(DeckImportError):
        archidekt.fetch_deck("https://archidekt.com/notadeck")


def test_archidekt_estimated_bracket(monkeypatch):
    # edhBracket null -> fall back to the site's estimated bracket (HTML scrape).
    api_payload = {
        "id": 55, "name": "Estimated Deck", "deckFormat": 3, "edhBracket": None,
        "cards": [
            {"quantity": 1, "categories": ["Commander"], "card": {"oracleCard": {"name": "Cmd"}}},
            {"quantity": 1, "categories": [], "card": {"oracleCard": {"name": "Sol Ring"}}},
        ],
    }
    html = '<button>Est<!-- --> Bracket:<!-- --> <!-- -->Upgraded (3)</button>'

    def fake_get(url, **kw):
        if "/api/decks/" in url:
            return _Resp(payload=api_payload)
        return _Resp(text=html)

    monkeypatch.setattr(archidekt, "safe_get", fake_get)
    deck = archidekt.fetch_deck("https://archidekt.com/decks/55")
    assert deck.bracket == 3
    assert deck.bracket_estimated is True


def test_archidekt_list_filters_to_username(monkeypatch):
    payload = {
        "count": 2,
        "next": None,
        "results": [
            {"id": 1, "name": "Mine", "deckFormat": 3,
             "owner": {"username": "Me"}, "edhBracket": 3, "colors": {"G": 2}},
            {"id": 2, "name": "SomeoneElse", "deckFormat": 3,
             "owner": {"username": "Stranger"}},
        ],
    }
    captured = {}

    def fake_get(url, **kw):
        captured["url"] = url
        return _Resp(payload=payload)

    monkeypatch.setattr(archidekt, "safe_get", fake_get)
    decks = archidekt.list_user_decks("Me")
    # Correct filter param, and foreign-owned decks are dropped.
    assert "ownerUsername=Me" in captured["url"]
    assert "owner=" not in captured["url"].replace("ownerUsername=", "")
    assert [d["name"] for d in decks] == ["Mine"]


def test_moxfield_fetch_v3(monkeypatch):
    payload = {
        "name": "Krenko Go Wide",
        "format": "commander",
        "colorIdentity": ["R"],
        "boards": {
            "commanders": {"cards": {"x": {"quantity": 1, "card": {"name": "Krenko, Mob Boss", "color_identity": ["R"]}}}},
            "mainboard": {"cards": {"y": {"quantity": 1, "card": {"name": "Goblin Bombardment", "color_identity": ["R"]}}}},
            "maybeboard": {"cards": {"z": {"quantity": 1, "card": {"name": "Not In Deck"}}}},
        },
    }
    monkeypatch.setattr(moxfield, "safe_get", lambda url, **kw: _Resp(payload=payload))
    deck = moxfield.fetch_deck("https://www.moxfield.com/decks/abc123")
    assert deck.name == "Krenko Go Wide"
    assert deck.commanders == ["Krenko, Mob Boss"]
    assert deck.color_identity == "R"
    assert {c["name"] for c in deck.cards} == {"Goblin Bombardment"}  # maybeboard excluded


def test_moxfield_bad_link():
    with pytest.raises(DeckImportError):
        moxfield.fetch_deck("https://www.moxfield.com/")


def test_moxfield_cloudflare_block_is_clear(monkeypatch):
    # Moxfield fronts its API with Cloudflare; a 403 HTML challenge must surface
    # a clear, honest message for both deck fetch and username listing.
    def blocked(url, **kw):
        return _Resp(text="<!DOCTYPE html>Attention Required! | Cloudflare", status=403)

    monkeypatch.setattr(moxfield, "safe_get", blocked)
    with pytest.raises(DeckImportError) as ei:
        moxfield.fetch_deck("https://www.moxfield.com/decks/abc123")
    assert "Cloudflare" in str(ei.value)
    with pytest.raises(DeckImportError):
        moxfield.list_user_decks("freikugel")


def test_goldfish_fetch(monkeypatch):
    download = "1 Sol Ring\n1 Arcane Signet\n\nCommander\n1 Kenrith, the Returned King\n"
    html = '<meta property="og:title" content="Superfriends Deck" />'

    def fake_get(url, **kw):
        if "download" in url:
            return _Resp(text=download)
        return _Resp(text=html)

    monkeypatch.setattr(mtggoldfish, "safe_get", fake_get)
    deck = mtggoldfish.fetch_deck("https://www.mtggoldfish.com/deck/12345")
    assert deck.commanders == ["Kenrith, the Returned King"]
    assert {c["name"] for c in deck.cards} == {"Sol Ring", "Arcane Signet"}
    assert deck.source == "mtggoldfish"

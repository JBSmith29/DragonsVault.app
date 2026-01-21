from core.domains.decks.services.proxy_decks import (
    fetch_goldfish_deck,
    _normalize_goldfish_url,
)


def test_normalize_goldfish_url_removes_fragment_and_defaults():
    cleaned = _normalize_goldfish_url("https://www.mtggoldfish.com/deck/123?foo=bar#section")
    assert cleaned == "https://www.mtggoldfish.com/deck/123?foo=bar"

    schemeless = _normalize_goldfish_url("//mtggoldfish.com/deck/view/456")
    assert schemeless == "https://mtggoldfish.com/deck/view/456"

    relative = _normalize_goldfish_url("/deck/789")
    assert relative == "https://www.mtggoldfish.com/deck/789"


def test_fetch_goldfish_deck_rejects_untrusted_host(monkeypatch):
    def _should_not_be_called(*args, **kwargs):  # pragma: no cover - defensive
        raise AssertionError("External request attempted for hostile host")

    monkeypatch.setattr("core.domains.decks.services.proxy_decks.requests.get", _should_not_be_called)

    deck_name, owner, commander, lines, errors = fetch_goldfish_deck("https://example.com/deck/123")

    assert deck_name is None
    assert owner is None
    assert commander is None
    assert lines == []
    assert errors and "mtggoldfish" in errors[0].lower()


def test_fetch_goldfish_deck_rejects_untrusted_port(monkeypatch):
    def _should_not_be_called(*args, **kwargs):  # pragma: no cover - defensive
        raise AssertionError("External request attempted for hostile port")

    monkeypatch.setattr("core.domains.decks.services.proxy_decks.requests.get", _should_not_be_called)

    _, _, _, lines, errors = fetch_goldfish_deck("https://www.mtggoldfish.com:444/deck/123")

    assert lines == []
    assert errors and "mtggoldfish" in errors[0].lower()

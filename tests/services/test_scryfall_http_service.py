from core.domains.cards.services import scryfall_http_service as http_service


def test_scryfall_session_reuses_built_session(monkeypatch):
    built = []

    class _Session:
        pass

    monkeypatch.setattr(http_service, "_session", None)
    monkeypatch.setattr(
        http_service.runtime_service,
        "build_scryfall_session",
        lambda **kwargs: built.append(kwargs) or _Session(),
    )

    first = http_service.scryfall_session()
    second = http_service.scryfall_session()

    assert first is second
    assert len(built) == 1


def test_get_default_cards_download_uri_uses_bulk_helpers(monkeypatch):
    monkeypatch.setattr(
        http_service.bulk,
        "get_default_cards_download_uri",
        lambda get_bulk_download_uri_fn: get_bulk_download_uri_fn("default_cards"),
    )
    monkeypatch.setattr(
        http_service.bulk,
        "get_bulk_download_uri",
        lambda kind, get_bulk_metadata_fn: get_bulk_metadata_fn(kind)["download_uri"],
    )
    monkeypatch.setattr(
        http_service.bulk,
        "get_bulk_metadata",
        lambda kind, fetch_bulk_index_fn: next(item for item in fetch_bulk_index_fn() if item["type"] == kind),
    )
    monkeypatch.setattr(
        http_service,
        "fetch_bulk_index",
        lambda: [{"type": "default_cards", "download_uri": "https://example.com/default.json"}],
    )

    assert http_service.get_default_cards_download_uri() == "https://example.com/default.json"

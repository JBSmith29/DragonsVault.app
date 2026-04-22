from models import Card, Folder, db
from core.domains.decks.services.proxy_decks import ResolvedCard


def _login(client, identifier, password):
    return client.post(
        "/login",
        data={"identifier": identifier, "password": password},
        follow_redirects=True,
    )


def test_create_proxy_deck_creates_proxy_folder(client, create_user, app, monkeypatch):
    from core.domains.decks.services import proxy_deck_service

    user, password = create_user(email="proxy-owner@example.com", username="proxy_owner")

    monkeypatch.setattr(proxy_deck_service, "_ensure_cache_ready", lambda: False)
    monkeypatch.setattr(
        proxy_deck_service,
        "resolve_proxy_cards",
        lambda lines: (
            [
                ResolvedCard(
                    name="Sol Ring",
                    quantity=2,
                    oracle_id="proxy-sol-ring",
                    set_code="CMM",
                    collector_number="100",
                    lang="en",
                )
            ],
            [],
        ),
    )

    _login(client, user.email, password)
    response = client.post(
        "/decks/proxy",
        data={"deck_name": "Proxy Deck", "decklist": "2 Sol Ring"},
        follow_redirects=False,
    )

    assert response.status_code == 302

    with app.app_context():
        folder = Folder.query.filter_by(owner_user_id=user.id, name="Proxy Deck").first()
        assert folder is not None
        assert folder.is_proxy is True
        card = Card.query.filter_by(folder_id=folder.id, name="Sol Ring").first()
        assert card is not None
        assert card.quantity == 2
        assert f"/folders/{folder.id}" in (response.headers.get("Location") or "")


def test_create_proxy_deck_bulk_imports_multiple_urls(client, create_user, app, monkeypatch):
    from core.domains.decks.services import proxy_deck_service

    user, password = create_user(email="proxy-bulk@example.com", username="proxy_bulk")

    monkeypatch.setattr(proxy_deck_service, "_ensure_cache_ready", lambda: False)
    monkeypatch.setattr(
        proxy_deck_service,
        "fetch_proxy_deck",
        lambda url: (
            f"Deck {url[-1].upper()}",
            "Bulk Owner",
            None,
            [f"1 Card {url[-1].upper()}"],
            [],
        ),
    )
    monkeypatch.setattr(
        proxy_deck_service,
        "resolve_proxy_cards",
        lambda lines: (
            [
                ResolvedCard(
                    name=lines[0].split(" ", 1)[1],
                    quantity=1,
                    oracle_id=f"oracle-{lines[0][-1].lower()}",
                    set_code="CMM",
                    collector_number="1",
                    lang="en",
                )
            ],
            [],
        ),
    )

    _login(client, user.email, password)
    response = client.post(
        "/decks/proxy/bulk",
        data={"deck_urls": "https://example.test/a\nhttps://example.test/b"},
        follow_redirects=True,
    )

    assert response.status_code == 200
    with app.app_context():
        folders = Folder.query.filter_by(owner_user_id=user.id, is_proxy=True).order_by(Folder.name.asc()).all()
        assert [folder.name for folder in folders] == ["Deck A", "Deck B"]


def test_api_fetch_proxy_deck_returns_payload(client, create_user, monkeypatch):
    from core.domains.decks.services import proxy_deck_service

    user, password = create_user(email="proxy-api@example.com", username="proxy_api")

    monkeypatch.setattr(
        proxy_deck_service,
        "fetch_proxy_deck",
        lambda url: ("Fetched Deck", "Deck Owner", "Atraxa", ["1 Sol Ring", "1 Arcane Signet"], []),
    )

    _login(client, user.email, password)
    response = client.post("/api/decks/proxy/fetch", json={"deck_url": "https://example.test/deck"})

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["ok"] is True
    assert payload["deck_name"] == "Fetched Deck"
    assert payload["owner"] == "Deck Owner"
    assert payload["commander"] == "Atraxa"
    assert payload["decklist"] == "1 Sol Ring\n1 Arcane Signet"

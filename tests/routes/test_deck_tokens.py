from models import Card, Folder, FolderRole, db


def test_deck_tokens_page_uses_token_stub_fallback(client, app, monkeypatch, create_user):
    from core.domains.decks.services import deck_tokens_service

    user, password = create_user(email="deck_tokens@example.com", username="deck_tokens")

    with app.app_context():
        deck = Folder(
            name="Token Deck",
            category=Folder.CATEGORY_DECK,
            owner_user_id=user.id,
        )
        db.session.add(deck)
        db.session.flush()
        db.session.add(FolderRole(folder_id=deck.id, role=FolderRole.ROLE_DECK))
        db.session.add(
            Card(
                name="Prosperous Card",
                set_code="TST",
                collector_number="1",
                folder_id=deck.id,
                quantity=1,
                lang="en",
                oracle_text="Create a Treasure token.",
            )
        )
        db.session.commit()

    monkeypatch.setattr(
        deck_tokens_service.sc,
        "search_tokens",
        lambda *args, **kwargs: [
            {
                "id": "token-treasure",
                "name": "Treasure",
                "type_line": "Token Artifact - Treasure",
                "images": {"small": "https://example.com/treasure-small.jpg"},
            }
        ],
    )

    client.post(
        "/login",
        data={"identifier": user.email, "password": password},
        follow_redirects=True,
    )

    response = client.get("/decks/tokens")
    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert "Treasure" in body
    assert "Token Deck" in body

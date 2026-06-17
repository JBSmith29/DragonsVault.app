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


def test_deck_tokens_keeps_same_name_tokens_of_different_colors_separate(
    client, app, monkeypatch, create_user
):
    """Same-name, same-P/T tokens of different colours must stay distinct so each
    image stays tied to its own colour label."""
    from core.domains.decks.services import deck_tokens_service

    user, password = create_user(email="ct@example.com", username="ct")

    with app.app_context():
        deck = Folder(name="Spirit Deck", category=Folder.CATEGORY_DECK, owner_user_id=user.id)
        db.session.add(deck)
        db.session.flush()
        db.session.add(FolderRole(folder_id=deck.id, role=FolderRole.ROLE_DECK))
        db.session.add(
            Card(
                name="Spirit Maker",
                set_code="TST",
                collector_number="1",
                folder_id=deck.id,
                quantity=1,
                lang="en",
                oracle_id="spirit-maker-oracle",
                oracle_text="Create a 1/1 white Spirit and a 1/1 black Spirit.",
            )
        )
        db.session.commit()

    monkeypatch.setattr(
        deck_tokens_service.sc,
        "tokens_from_oracle",
        lambda oracle_id: [
            {
                "id": "spirit-white",
                "name": "Spirit",
                "type_line": "Token Creature — Spirit",
                "power": "1",
                "toughness": "1",
                "colors": ["W"],
                "images": {"small": "https://example.com/spirit-white.jpg"},
            },
            {
                "id": "spirit-black",
                "name": "Spirit",
                "type_line": "Token Creature — Spirit",
                "power": "1",
                "toughness": "1",
                "colors": ["B"],
                "images": {"small": "https://example.com/spirit-black.jpg"},
            },
        ],
    )

    client.post(
        "/login",
        data={"identifier": user.email, "password": password},
        follow_redirects=True,
    )

    body = client.get("/decks/tokens").get_data(as_text=True)
    # Two distinct token cards, each with its own colour label and image.
    assert "White" in body and "Black" in body
    assert "spirit-white.jpg" in body and "spirit-black.jpg" in body

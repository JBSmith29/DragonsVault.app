from models import Card, Folder, FolderRole, db


def _login(client, identifier, password):
    return client.post(
        "/login",
        data={"identifier": identifier, "password": password},
        follow_redirects=True,
    )


def _create_owned_card(app, owner, *, deck_name="Detail Deck", card_name="Detail Card"):
    with app.app_context():
        deck = Folder(
            name=deck_name,
            category=Folder.CATEGORY_DECK,
            owner_user_id=owner.id,
        )
        db.session.add(deck)
        db.session.flush()
        db.session.add(FolderRole(folder_id=deck.id, role=FolderRole.ROLE_DECK))

        card = Card(
            name=card_name,
            set_code="TST",
            collector_number="1",
            folder_id=deck.id,
            quantity=1,
            lang="en",
        )
        db.session.add(card)
        db.session.commit()
        return card.id


def test_owned_card_detail_renders(client, create_user, app):
    user, password = create_user(email="detail-owner@example.com", username="detail_owner")
    card_id = _create_owned_card(app, user)

    _login(client, user.email, password)
    resp = client.get(f"/cards/{card_id}")

    assert resp.status_code == 200
    assert b"Detail Card" in resp.data


def test_smart_card_detail_redirects_to_scryfall_print(client, create_user):
    user, password = create_user(email="detail-redirect@example.com", username="detail_redirect")
    _login(client, user.email, password)

    resp = client.get("/cards/test-scryfall-id")

    assert resp.status_code in (301, 302)
    location = resp.headers.get("Location") or ""
    assert "/scryfall/print/test-scryfall-id" in location

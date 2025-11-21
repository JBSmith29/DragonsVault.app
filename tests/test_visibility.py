from models import Card, Folder, db


def _login(client, identifier, password):
    return client.post(
        "/login",
        data={"identifier": identifier, "password": password},
        follow_redirects=True,
    )


def _create_owned_folder(app, owner, *, name="Owner Deck", card_name="Secret Tech"):
    with app.app_context():
        folder = Folder(name=name, category=Folder.CATEGORY_DECK, owner_user_id=owner.id)
        db.session.add(folder)
        db.session.flush()
        card = Card(
            name=card_name,
            set_code="TST",
            collector_number="1",
            folder_id=folder.id,
            quantity=1,
            lang="en",
        )
        db.session.add(card)
        db.session.commit()
        return folder, card


def test_user_cannot_view_other_users_collection(client, create_user, app):
    owner, owner_password = create_user(email="owner@example.com", username="owneruser")
    viewer, viewer_password = create_user(email="viewer@example.com", username="vieweruser")
    folder, card = _create_owned_folder(app, owner)

    _login(client, viewer.email, viewer_password)

    cards_resp = client.get("/cards")
    assert cards_resp.status_code == 200
    assert card.name.encode() not in cards_resp.data

    folder_resp = client.get(f"/folders/{folder.id}")
    assert folder_resp.status_code in (403, 404)


def test_admin_cannot_view_other_users_collections(client, create_user, app):
    owner, owner_password = create_user(email="deckowner@example.com", username="deckowner")
    admin, admin_password = create_user(
        email="admin@example.com",
        username="adminuser",
        is_admin=True,
    )
    _, card = _create_owned_folder(app, owner, name="Admin Deck", card_name="Visible Card")

    _login(client, admin.email, admin_password)

    resp = client.get("/cards")
    assert resp.status_code == 200
    assert card.name.encode() not in resp.data

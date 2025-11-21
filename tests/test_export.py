from models import Card, Folder, db


def _login(client, identifier, password):
    return client.post(
        "/login",
        data={"identifier": identifier, "password": password},
        follow_redirects=True,
    )


def _create_folder_with_card(app, owner, *, name, card_name):
    with app.app_context():
        folder = Folder(name=name, category=Folder.CATEGORY_DECK, owner_user_id=owner.id)
        db.session.add(folder)
        db.session.flush()
        card = Card(
            name=card_name,
            set_code="EXP",
            collector_number="1",
            folder_id=folder.id,
            quantity=1,
            lang="en",
        )
        db.session.add(card)
        db.session.commit()
        return folder, card


def test_export_selected_folders_manabox(client, create_user, app):
    user, password = create_user(email="exporter@example.com")
    folder_one, card_one = _create_folder_with_card(app, user, name="Folder One", card_name="First Card")
    folder_two, card_two = _create_folder_with_card(app, user, name="Folder Two", card_name="Second Card")

    _login(client, user.email, password)

    resp = client.get(
        f"/cards/export?format=manabox&folder_ids={folder_one.id}&folder_ids={folder_two.id}",
    )
    assert resp.status_code == 200
    assert resp.headers.get("Content-Disposition") and "manabox" in resp.headers["Content-Disposition"]
    payload = resp.data.decode("utf-8")
    assert "First Card" in payload
    assert "Second Card" in payload


def test_export_all_folders_dragonshield(client, create_user, app):
    user, password = create_user(email="dragonshield@example.com")
    _, card = _create_folder_with_card(app, user, name="My Deck", card_name="Dragon Card")

    _login(client, user.email, password)

    resp = client.get("/cards/export?format=dragonshield&all_folders=1")
    assert resp.status_code == 200
    assert resp.headers.get("Content-Disposition") and "dragonshield" in resp.headers["Content-Disposition"]
    assert card.name in resp.data.decode("utf-8")

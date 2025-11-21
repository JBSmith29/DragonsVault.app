from models import Card, Folder, db


def _login(client, email, password):
    return client.post(
        "/login",
        data={"identifier": email, "password": password},
        follow_redirects=True,
    )


def test_manual_import_creates_cards(client, create_user, app):
    user, password = create_user(email="manual@example.com", username="manualuser")
    _login(client, user.email, password)

    payload = {
        "action": "import",
        "entry_ids": "0",
        "entry-0-name": "Manual Test Card",
        "entry-0-quantity": "2",
        "entry-0-printing": "TST::001::EN",
        "entry-0-finish": "nonfoil",
        "entry-0-folder_name": "Manual Folder",
    }

    resp = client.post("/import/manual", data=payload, follow_redirects=True)
    assert resp.status_code == 200

    with app.app_context():
        folder = Folder.query.filter_by(name="Manual Folder", owner_user_id=user.id).first()
        assert folder is not None
        card = Card.query.filter_by(folder_id=folder.id, name="Manual Test Card").first()
        assert card is not None
        assert card.quantity == 2

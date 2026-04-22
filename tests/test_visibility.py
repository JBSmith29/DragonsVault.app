from models import Card, Folder, FolderShare, User, UserFriend, db


def _login(client, identifier, password):
    return client.post(
        "/login",
        data={"identifier": identifier, "password": password},
        follow_redirects=True,
    )


def _create_owned_folder(
    app,
    owner,
    *,
    name="Owner Deck",
    card_name="Secret Tech",
    shared_user=None,
    is_public=False,
):
    with app.app_context():
        folder = Folder(
            name=name,
            category=Folder.CATEGORY_DECK,
            owner_user_id=owner.id if owner is not None else None,
            is_public=is_public,
        )
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
        if shared_user is not None:
            db.session.add(FolderShare(folder_id=folder.id, shared_user_id=shared_user.id))
        db.session.commit()
        return folder, card


def _issue_api_token(app, user_id: int) -> str:
    with app.app_context():
        user = db.session.get(User, user_id)
        assert user is not None
        token = user.issue_api_token()
        db.session.commit()
        return token


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


def test_user_cannot_access_other_users_card_detail_or_insight(client, create_user, app):
    owner, _owner_password = create_user(email="owner-api@example.com", username="owner_api")
    viewer, viewer_password = create_user(email="viewer-api@example.com", username="viewer_api")
    folder, card = _create_owned_folder(app, owner, name="Private Deck", card_name="Hidden Oracle")

    _login(client, viewer.email, viewer_password)

    api_card_resp = client.get(f"/api/card/{card.id}")
    assert api_card_resp.status_code in (403, 404)

    detail_resp = client.get(f"/cards/{card.id}")
    assert detail_resp.status_code in (403, 404)

    insight_resp = client.get(f"/api/decks/{folder.id}/insight")
    assert insight_resp.status_code in (403, 404)


def test_bearer_token_cannot_access_other_users_card_or_deck(client, create_user, app):
    owner, _owner_password = create_user(email="owner-token@example.com", username="owner_token")
    viewer, _viewer_password = create_user(email="viewer-token@example.com", username="viewer_token")
    folder, card = _create_owned_folder(app, owner, name="Token Private Deck", card_name="Token Hidden")

    token = _issue_api_token(app, viewer.id)
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}

    me_resp = client.get("/api/me", headers=headers)
    assert me_resp.status_code == 200

    api_card_resp = client.get(f"/api/card/{card.id}", headers=headers)
    assert api_card_resp.status_code in (403, 404)

    insight_resp = client.get(f"/api/decks/{folder.id}/insight", headers=headers)
    assert insight_resp.status_code in (403, 404)


def test_api_folder_endpoints_allow_friend_access_via_bearer_token(client, create_user, app):
    owner, _owner_password = create_user(email="friend-owner@example.com", username="friend_owner")
    viewer, _viewer_password = create_user(email="friend-viewer@example.com", username="friend_viewer")
    folder, card = _create_owned_folder(
        app,
        owner,
        name="Friend Deck",
        card_name="Friendly Secret",
    )
    with app.app_context():
        db.session.add(UserFriend(user_id=viewer.id, friend_user_id=owner.id))
        db.session.commit()

    token = _issue_api_token(app, viewer.id)
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}

    folders_resp = client.get("/api/folders", headers=headers)
    assert folders_resp.status_code == 200
    folder_payload = folders_resp.get_json() or {}
    folder_ids = {item["id"] for item in folder_payload.get("data", [])}
    assert folder.id in folder_ids

    detail_resp = client.get(f"/api/folders/{folder.id}", headers=headers)
    assert detail_resp.status_code == 200
    detail_payload = detail_resp.get_json() or {}
    assert detail_payload.get("data", {}).get("name") == "Friend Deck"

    cards_resp = client.get(f"/api/folders/{folder.id}/cards", headers=headers)
    assert cards_resp.status_code == 200
    cards_payload = cards_resp.get_json() or {}
    card_names = [item["name"] for item in cards_payload.get("data", [])]
    assert card.name in card_names


def test_api_folder_endpoints_allow_ownerless_folder_access(client, create_user, app):
    viewer, viewer_password = create_user(email="ownerless-viewer@example.com", username="ownerless_viewer")
    folder, card = _create_owned_folder(
        app,
        None,
        name="System Deck",
        card_name="Open Secret",
    )

    _login(client, viewer.email, viewer_password)

    folders_resp = client.get("/api/folders")
    assert folders_resp.status_code == 200
    folder_payload = folders_resp.get_json() or {}
    folder_ids = {item["id"] for item in folder_payload.get("data", [])}
    assert folder.id in folder_ids

    detail_resp = client.get(f"/api/folders/{folder.id}")
    assert detail_resp.status_code == 200
    detail_payload = detail_resp.get_json() or {}
    assert detail_payload.get("data", {}).get("name") == "System Deck"

    cards_resp = client.get(f"/api/folders/{folder.id}/cards")
    assert cards_resp.status_code == 200
    cards_payload = cards_resp.get_json() or {}
    card_names = [item["name"] for item in cards_payload.get("data", [])]
    assert card.name in card_names


def test_api_card_response_uses_private_cache_control(client, create_user, app):
    owner, owner_password = create_user(email="owner-cache@example.com", username="owner_cache")
    _folder, card = _create_owned_folder(app, owner, name="Cache Deck", card_name="Cache Hidden")
    _login(client, owner.email, owner_password)

    resp = client.get(f"/api/card/{card.id}")
    assert resp.status_code == 200
    cache_control = (resp.headers.get("Cache-Control") or "").lower()
    assert "private" in cache_control
    assert "public" not in cache_control

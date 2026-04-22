from models import Folder, FolderRole, FolderShare, db


def _login(client, identifier, password):
    return client.post(
        "/login",
        data={"identifier": identifier, "password": password},
        follow_redirects=True,
    )


def _create_deck(app, *, owner_user_id: int, name: str) -> int:
    with app.app_context():
        deck = Folder(
            name=name,
            category=Folder.CATEGORY_DECK,
            owner_user_id=owner_user_id,
        )
        db.session.add(deck)
        db.session.flush()
        db.session.add(FolderRole(folder_id=deck.id, role=FolderRole.ROLE_DECK))
        db.session.commit()
        return deck.id


def test_folder_sharing_toggle_public_updates_folder(client, create_user, app):
    user, password = create_user(email="share-owner@example.com", username="share_owner")
    deck_id = _create_deck(app, owner_user_id=user.id, name="Share Deck")

    _login(client, user.email, password)
    response = client.post(
        f"/folders/{deck_id}/sharing",
        data={"action": "toggle_public", "state": "1"},
        follow_redirects=False,
    )

    assert response.status_code == 302
    with app.app_context():
        folder = db.session.get(Folder, deck_id)
        assert folder is not None
        assert folder.is_public is True


def test_folder_sharing_add_share_by_username_creates_share(client, create_user, app):
    owner, password = create_user(email="share-source@example.com", username="share_source")
    target, _ = create_user(email="share-target@example.com", username="share_target")
    deck_id = _create_deck(app, owner_user_id=owner.id, name="Invite Deck")

    _login(client, owner.email, password)
    response = client.post(
        f"/folders/{deck_id}/sharing",
        data={"action": "add_share", "share_identifier": target.username},
        follow_redirects=False,
    )

    assert response.status_code == 302
    with app.app_context():
        share = FolderShare.query.filter_by(folder_id=deck_id, shared_user_id=target.id).first()
        assert share is not None


def test_folder_sharing_remove_share_deletes_access(client, create_user, app):
    owner, password = create_user(email="share-remove-owner@example.com", username="share_remove_owner")
    target, _ = create_user(email="share-remove-target@example.com", username="share_remove_target")
    deck_id = _create_deck(app, owner_user_id=owner.id, name="Remove Deck")

    with app.app_context():
        share = FolderShare(folder_id=deck_id, shared_user_id=target.id)
        db.session.add(share)
        db.session.commit()
        share_id = share.id

    _login(client, owner.email, password)
    response = client.post(
        f"/folders/{deck_id}/sharing",
        data={"action": "remove_share", "share_id": str(share_id)},
        follow_redirects=False,
    )

    assert response.status_code == 302
    with app.app_context():
        share = db.session.get(FolderShare, share_id)
        assert share is None

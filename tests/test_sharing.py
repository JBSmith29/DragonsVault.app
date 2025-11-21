from models import Folder, FolderShare, db


def _login(client, identifier, password):
    return client.post(
        "/login",
        data={"identifier": identifier, "password": password},
        follow_redirects=True,
    )


def test_shared_folder_visible_to_invited_user(client, create_user, app):
    owner, owner_password = create_user(email="owner@example.com", username="owner")
    viewer, viewer_password = create_user(email="viewer@example.com", username="viewer")

    with app.app_context():
        folder = Folder(name="Shared Deck", category=Folder.CATEGORY_DECK, owner_user_id=owner.id)
        db.session.add(folder)
        db.session.flush()
        share = FolderShare(folder_id=folder.id, shared_user_id=viewer.id)
        db.session.add(share)
        db.session.commit()
        shared_id = folder.id

    _login(client, viewer.email, viewer_password)

    resp = client.get("/cards/shared")
    assert resp.status_code == 200
    assert b"Shared Deck" in resp.data

    resp2 = client.get(f"/shared/folder/{shared_id}")
    assert resp2.status_code == 200
    assert b"Shared Deck" in resp2.data


def test_public_shared_folder_via_token(client, create_user, app):
    owner, owner_password = create_user(email="owner2@example.com", username="owner2")
    viewer, viewer_password = create_user(email="viewer2@example.com", username="viewer2")

    with app.app_context():
        folder = Folder(name="Public Deck", category=Folder.CATEGORY_DECK, owner_user_id=owner.id, is_public=True)
        folder.ensure_share_token()
        db.session.add(folder)
        db.session.commit()
        token = folder.share_token

    _login(client, viewer.email, viewer_password)

    resp = client.get(f"/shared/{token}")
    assert resp.status_code == 200
    assert b"Public Deck" in resp.data

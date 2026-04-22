from models import Card, Folder, FolderRole, UserFriend, db


def _login(client, identifier, password):
    return client.post(
        "/login",
        data={"identifier": identifier, "password": password},
        follow_redirects=True,
    )


def test_list_checker_post_reports_owned_friend_and_basic_land_rows(client, create_user, app, monkeypatch):
    from core.domains.cards.services import list_checker_service

    viewer, viewer_password = create_user(
        email="viewer-list-checker@example.com",
        username="viewer-list-checker",
    )
    friend, _ = create_user(
        email="friend-list-checker@example.com",
        username="friend-list-checker",
        display_name="Friend Collector",
    )

    with app.app_context():
        viewer_folder = Folder(
            name="Main Binder",
            category=Folder.CATEGORY_COLLECTION,
            owner_user_id=viewer.id,
        )
        friend_folder = Folder(
            name="Friend Binder",
            category=Folder.CATEGORY_COLLECTION,
            owner_user_id=friend.id,
        )
        db.session.add_all(
            [
                viewer_folder,
                friend_folder,
                UserFriend(user_id=viewer.id, friend_user_id=friend.id),
            ]
        )
        db.session.flush()
        db.session.add_all(
            [
                FolderRole(folder_id=viewer_folder.id, role=FolderRole.ROLE_COLLECTION),
                FolderRole(folder_id=friend_folder.id, role=FolderRole.ROLE_COLLECTION),
                Card(
                    name="Sol Ring",
                    set_code="CMM",
                    collector_number="1",
                    folder_id=viewer_folder.id,
                    quantity=1,
                    oracle_id="oid-sol-ring",
                    lang="en",
                ),
                Card(
                    name="Arcane Signet",
                    set_code="CMM",
                    collector_number="2",
                    folder_id=friend_folder.id,
                    quantity=1,
                    oracle_id="oid-arcane-signet",
                    lang="en",
                ),
            ]
        )
        db.session.commit()

    monkeypatch.setattr(list_checker_service.scryfall_cache, "ensure_cache_loaded", lambda: False)

    _login(client, viewer.email, viewer_password)
    response = client.post(
        "/list-checker",
        data={"card_list": "2 Sol Ring\nArcane Signet\nIsland"},
        follow_redirects=True,
    )

    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert "Have all: 1" in body
    assert "Partial: 1" in body
    assert "In Friends Collection: 1" in body
    assert "Missing: 0" in body
    assert "Sol Ring" in body
    assert "Arcane Signet" in body
    assert "Island" in body
    assert "Main Binder" in body
    assert "Friend Collector: Friend Binder" in body


def test_list_checker_export_csv_returns_download(client, create_user, app, monkeypatch):
    from core.domains.cards.services import list_checker_service

    user, password = create_user(
        email="export-list-checker@example.com",
        username="export-list-checker",
    )

    with app.app_context():
        folder = Folder(
            name="Owned Collection",
            category=Folder.CATEGORY_COLLECTION,
            owner_user_id=user.id,
        )
        db.session.add(folder)
        db.session.flush()
        db.session.add(FolderRole(folder_id=folder.id, role=FolderRole.ROLE_COLLECTION))
        db.session.add(
            Card(
                name="Mind Stone",
                set_code="WOC",
                collector_number="1",
                folder_id=folder.id,
                quantity=1,
                oracle_id="oid-mind-stone",
                lang="en",
                type_line="Artifact",
                rarity="common",
                color_identity="",
            )
        )
        db.session.commit()

    monkeypatch.setattr(list_checker_service.scryfall_cache, "ensure_cache_loaded", lambda: False)

    _login(client, user.email, password)
    response = client.post(
        "/list-checker/export",
        data={"card_list": "Mind Stone"},
        follow_redirects=False,
    )

    assert response.status_code == 200
    assert response.headers["Content-Disposition"] == "attachment; filename=list_checker_results.csv"
    body = response.get_data(as_text=True)
    assert body.startswith("\ufeffCard,Type,Color Identity,Rarity,Requested,Available,Missing,Status,Total Owned,Collection 1")
    assert "Mind Stone,Artifact,—,Common,1,1,0,have_all,1,Owned Collection ×1" in body

from models import Card, Folder, FolderRole, UserFriend, db


def test_deck_list_uses_commander_placeholder(client, app, monkeypatch, db_session, create_user):
    from core.domains.decks.services import deck_gallery_service

    user, password = create_user(email="decks@example.com")

    with app.app_context():
        folder = Folder(
            name="Proxy Deck",
            category=Folder.CATEGORY_DECK,
            owner_user_id=user.id,
            is_proxy=True,
            commander_name="Offline Commander",
            commander_oracle_id=None,
        )
        db.session.add(folder)
        db.session.flush()
        db.session.add(FolderRole(folder_id=folder.id, role=FolderRole.ROLE_DECK))

        card = Card(
            name="Forest",
            set_code="LTR",
            collector_number="278",
            folder_id=folder.id,
            quantity=1,
            lang="en",
        )
        db.session.add(card)
        db.session.commit()
    client.post(
        "/login",
        data={"identifier": user.email, "password": password},
        follow_redirects=True,
    )

    monkeypatch.setattr(deck_gallery_service, "evaluate_commander_bracket", lambda *args, **kwargs: {})
    monkeypatch.setattr(deck_gallery_service, "prints_for_oracle", lambda *args, **kwargs: ())
    monkeypatch.setattr(deck_gallery_service, "_lookup_print_data", lambda *args, **kwargs: {})

    response = client.get("/decks")
    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert "Offline Commander" in body
    assert "cmdr-thumb-placeholder" in body


def test_cards_type_filter_uses_cache_fallback(client, app, monkeypatch, create_user, db_session):
    from core.domains.cards.services import collection_query_service
    user, password = create_user(email="collection@example.com")

    with app.app_context():
        folder = Folder(name="My Collection", category=Folder.CATEGORY_COLLECTION, owner_user_id=user.id)
        db.session.add(folder)
        db.session.flush()
        creature = Card(
            name="Invisible Stalker",
            set_code="ISD",
            collector_number="63",
            folder_id=folder.id,
            quantity=1,
            oracle_id="fake-creature",
            type_line=None,
        )
        db.session.add(creature)
        db.session.commit()

    client.post(
        "/login",
        data={"identifier": user.email, "password": password},
        follow_redirects=True,
    )

    monkeypatch.setattr(collection_query_service.sc, "cache_ready", lambda: True)
    monkeypatch.setattr(
        collection_query_service,
        "_bulk_print_lookup",
        lambda cards, **kwargs: {creature.id: {"type_line": "Creature — Human Rogue"}},
    )

    response = client.get("/cards?type=creature")
    assert response.status_code == 200
    assert "Invisible Stalker" in response.get_data(as_text=True)


def test_collection_overview_show_friends_includes_friend_bucket(client, create_user):
    viewer, viewer_password = create_user(
        email="viewer-collection@example.com",
        username="viewer-collection",
    )
    friend, _ = create_user(
        email="friend-collection@example.com",
        username="friend-collection",
        display_name="Friend Collector",
    )

    friend_folder = Folder(
        name="Friend Binder",
        category=Folder.CATEGORY_COLLECTION,
        owner_user_id=friend.id,
    )
    db.session.add_all(
        [
            UserFriend(user_id=viewer.id, friend_user_id=friend.id),
            UserFriend(user_id=friend.id, friend_user_id=viewer.id),
            friend_folder,
        ]
    )
    db.session.flush()
    db.session.add(FolderRole(folder_id=friend_folder.id, role=FolderRole.ROLE_COLLECTION))
    db.session.commit()

    client.post(
        "/login",
        data={"identifier": viewer.email, "password": viewer_password},
        follow_redirects=True,
    )

    response = client.get("/collection?show_friends=1")

    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "Friend Binder" in html
    assert "Friend Collector" in html

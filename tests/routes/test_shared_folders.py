from models import Folder, FolderShare, UserFriend, UserFriendRequest, db


def _login(client, user, password):
    client.post(
        "/login",
        data={"identifier": user.email, "password": password},
        follow_redirects=True,
    )


def test_shared_folders_page_groups_friend_shared_and_public_folders(client, create_user):
    viewer, viewer_password = create_user(
        email="viewer@example.com",
        username="viewer",
        display_name="Viewer",
    )
    friend, _ = create_user(
        email="friend@example.com",
        username="friend",
        display_name="Friendly Owner",
    )
    sharer, _ = create_user(
        email="sharer@example.com",
        username="sharer",
        display_name="Sharer",
    )
    stranger, _ = create_user(
        email="stranger@example.com",
        username="stranger",
        display_name="Stranger",
    )

    db.session.add_all(
        [
            UserFriend(user_id=viewer.id, friend_user_id=friend.id),
            UserFriend(user_id=friend.id, friend_user_id=viewer.id),
        ]
    )
    db.session.flush()

    friend_folder = Folder(name="Friend Deck", category=Folder.CATEGORY_DECK, owner_user_id=friend.id)
    friend_public_folder = Folder(
        name="Friend Public Deck",
        category=Folder.CATEGORY_DECK,
        owner_user_id=friend.id,
        is_public=True,
    )
    shared_folder = Folder(
        name="Shared Binder",
        category=Folder.CATEGORY_COLLECTION,
        owner_user_id=sharer.id,
    )
    my_public_folder = Folder(
        name="My Public Deck",
        category=Folder.CATEGORY_DECK,
        owner_user_id=viewer.id,
        is_public=True,
    )
    other_public_folder = Folder(
        name="Community Binder",
        category=Folder.CATEGORY_COLLECTION,
        owner_user_id=stranger.id,
        is_public=True,
    )
    db.session.add_all(
        [
            friend_folder,
            friend_public_folder,
            shared_folder,
            my_public_folder,
            other_public_folder,
        ]
    )
    db.session.flush()
    db.session.add(FolderShare(folder_id=shared_folder.id, shared_user_id=viewer.id))
    db.session.commit()

    _login(client, viewer, viewer_password)

    response = client.get("/cards/shared")

    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "From friends" in html
    assert "Shared with me" in html
    assert "My public folders" in html
    assert "Public folders" in html
    assert "Friend Deck" in html
    assert "Shared Binder" in html
    assert "My Public Deck" in html
    assert "Community Binder" in html
    assert html.count("Friend Public Deck") == 1


def test_shared_follow_request_and_accept_creates_friendship(client, create_user):
    requester, requester_password = create_user(
        email="requester@example.com",
        username="requester",
    )
    recipient, recipient_password = create_user(
        email="recipient@example.com",
        username="recipient",
    )

    _login(client, requester, requester_password)
    response = client.post(
        "/cards/shared/follow",
        data={"action": "request", "friend_identifier": recipient.username},
        follow_redirects=False,
    )

    assert response.status_code == 302
    friend_request = UserFriendRequest.query.filter_by(
        requester_user_id=requester.id,
        recipient_user_id=recipient.id,
    ).first()
    assert friend_request is not None

    client.get("/logout", follow_redirects=True)
    _login(client, recipient, recipient_password)

    response = client.post(
        "/cards/shared/follow",
        data={"action": "accept", "request_id": str(friend_request.id)},
        follow_redirects=False,
    )

    assert response.status_code == 302
    assert UserFriendRequest.query.filter_by(id=friend_request.id).first() is None
    assert UserFriend.query.filter_by(user_id=requester.id, friend_user_id=recipient.id).first() is not None
    assert UserFriend.query.filter_by(user_id=recipient.id, friend_user_id=requester.id).first() is not None


def test_shared_follow_remove_deletes_both_friendship_rows(client, create_user):
    viewer, viewer_password = create_user(
        email="remove-viewer@example.com",
        username="remove-viewer",
    )
    friend, _ = create_user(
        email="remove-friend@example.com",
        username="remove-friend",
    )
    db.session.add_all(
        [
            UserFriend(user_id=viewer.id, friend_user_id=friend.id),
            UserFriend(user_id=friend.id, friend_user_id=viewer.id),
        ]
    )
    db.session.commit()

    _login(client, viewer, viewer_password)
    response = client.post(
        "/cards/shared/follow",
        data={"action": "remove", "friend_user_id": str(friend.id)},
        follow_redirects=False,
    )

    assert response.status_code == 302
    assert UserFriend.query.filter_by(user_id=viewer.id, friend_user_id=friend.id).first() is None
    assert UserFriend.query.filter_by(user_id=friend.id, friend_user_id=viewer.id).first() is None

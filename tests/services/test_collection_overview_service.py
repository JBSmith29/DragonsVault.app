from flask_login import login_user

from models import Folder, FolderRole, User, UserFriend, db


def test_collection_overview_show_friends_includes_friend_bucket(app, create_user, db_session):
    from core.domains.cards.services import collection_overview_service

    viewer, _viewer_password = create_user(
        email="viewer-collection-service@example.com",
        username="viewer-collection-service",
    )
    friend, _friend_password = create_user(
        email="friend-collection-service@example.com",
        username="friend-collection-service",
        display_name="Friend Collector",
    )

    with app.app_context():
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

        viewer = db.session.get(User, viewer.id)
        with app.test_request_context("/collection?show_friends=1"):
            login_user(viewer)
            html = collection_overview_service.collection_overview()

    assert "Friend Binder" in html
    assert "Friend Collector" in html


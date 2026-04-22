from collections import OrderedDict

from flask_login import login_user

from models import Card, Folder, FolderRole, User, db


def test_build_inventory_snapshot_counts_owned_collection_rows(app, create_user):
    from core.domains.cards.services import list_checker_inventory_service

    user, _password = create_user(
        email="list-checker-inventory@example.com",
        username="list-checker-inventory",
    )

    with app.app_context():
        folder = Folder(
            name="Main Binder",
            category=Folder.CATEGORY_COLLECTION,
            owner_user_id=user.id,
        )
        db.session.add(folder)
        db.session.flush()
        db.session.add(FolderRole(folder_id=folder.id, role=FolderRole.ROLE_COLLECTION))
        db.session.add(
            Card(
                name="Sol Ring",
                set_code="CMM",
                collector_number="1",
                folder_id=folder.id,
                quantity=1,
                oracle_id="oid-sol-ring",
                lang="en",
            )
        )
        db.session.commit()

        user = db.session.get(User, user.id)
        want = OrderedDict([("sol ring", {"display": "Sol Ring", "qty": 1})])

        with app.test_request_context("/list-checker"):
            login_user(user)
            snapshot = list_checker_inventory_service.build_inventory_snapshot(
                want,
                {"sol ring": "Sol Ring"},
            )

    assert snapshot.current_user_id == user.id
    assert snapshot.collection_id_set == {folder.id}
    assert snapshot.available_count["sol ring"] == 1
    assert snapshot.per_folder_counts["sol ring"][folder.id] == 1
    assert snapshot.folder_meta[folder.id]["name"] == "Main Binder"
    assert snapshot.rep_card_map["sol ring"].name == "Sol Ring"


def test_build_inventory_snapshot_rescues_face_names(app, create_user):
    from core.domains.cards.services import list_checker_inventory_service

    user, _password = create_user(
        email="list-checker-faces@example.com",
        username="list-checker-faces",
    )

    with app.app_context():
        folder = Folder(
            name="Spells Binder",
            category=Folder.CATEGORY_COLLECTION,
            owner_user_id=user.id,
        )
        db.session.add(folder)
        db.session.flush()
        db.session.add(FolderRole(folder_id=folder.id, role=FolderRole.ROLE_COLLECTION))
        db.session.add(
            Card(
                name="Fire // Ice",
                set_code="CMM",
                collector_number="2",
                folder_id=folder.id,
                quantity=1,
                oracle_id="oid-fire-ice",
                lang="en",
            )
        )
        db.session.commit()

        user = db.session.get(User, user.id)
        want = OrderedDict([("fire", {"display": "Fire", "qty": 1})])

        with app.test_request_context("/list-checker"):
            login_user(user)
            snapshot = list_checker_inventory_service.build_inventory_snapshot(
                want,
                {"fire": "Fire"},
            )

    assert snapshot.available_count["fire"] == 1
    assert snapshot.per_folder_counts["fire"][folder.id] == 1
    assert snapshot.rep_card_map["fire"].name == "Fire // Ice"
